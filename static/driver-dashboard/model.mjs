/** Read-only adapter for /api/state and /ws. Missing packet fields remain unavailable. */
export const finite=value=>typeof value==='number'&&Number.isFinite(value)?value:null;
export const positive=value=>finite(value)>0?value:null;
export const escapeHTML=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function lapTime(ms){if(positive(ms)===null)return '—';const n=Math.round(ms);return `${Math.floor(n/60000)}:${String(Math.floor(n%60000/1000)).padStart(2,'0')}.${String(n%1000).padStart(3,'0')}`}
export function fixed(n,d=1){return finite(n)===null?'—':n.toFixed(d)}
export function signed(n,d=3){return finite(n)===null?'—':`${n<0?'−':'+'}${Math.abs(n).toFixed(d)}`}
const makeFlag=(id,color,title,instruction,extra='')=>({id,color,title,instruction,extra});
export function raceFlag(s,{fresh=true}={}) {
  if(!fresh||!s)return makeFlag('stale','#ff6969','TELEMETRY UNAVAILABLE','Awaiting current race data.');
  const p=String(s.race_control_phase||''),f=String(s.fia_flag||'');
  if(s.red_flag_active||p==='red_flag'||f==='red')return makeFlag('red','#ff6969','RED FLAG','Session suspended. Follow race control.');
  if(s.driving_wrong_way)return makeFlag('wrong-way','#ff6969','WRONG WAY','Check your direction of travel.');
  if(p==='safety_car'||p==='safety_car_ending'||s.safety_car==='full')return makeFlag('sc','#ffc15c',p.endsWith('ending')?'SAFETY CAR ENDING':'SAFETY CAR','No overtaking. Follow race control.',s.safety_car_delta_valid?'SC delta '+signed(s.safety_car_delta_s)+' s':'');
  if(p==='vsc'||p==='vsc_ending'||s.safety_car==='virtual')return makeFlag('vsc','#ffc15c',p.endsWith('ending')?'VSC ENDING':'VIRTUAL SAFETY CAR','Reduce speed. Keep the delta positive.',s.safety_car_delta_valid?'VSC delta '+signed(s.safety_car_delta_s)+' s':'');
  if(f==='double_yellow')return makeFlag('double-yellow','#ffc15c','DOUBLE YELLOW','Reduce speed. Be prepared to stop.');
  if(f==='yellow')return makeFlag('yellow','#ffc15c','YELLOW FLAG','Slow down. No overtaking.');
  if(f==='black')return makeFlag('black','#eef4f8','BLACK FLAG','Follow race control instructions.');
  if(f==='chequered'||s.final_classification?.classification?.length)return makeFlag('finish','#eef4f8','CHEQUERED FLAG','Session complete. Follow race control.');
  if(s.unserved_drive_through_penalties>0)return makeFlag('penalty','#ffc15c','DRIVE-THROUGH','Serve the outstanding drive-through penalty.');
  if(s.unserved_stop_go_penalties>0)return makeFlag('penalty','#ffc15c','STOP-GO PENALTY','Serve the outstanding stop-go penalty.');
  if(s.game_paused)return makeFlag('paused','#91a6b8','GAME PAUSED','Showing the last received state.');
  if(f==='blue')return makeFlag('blue','#559aff','BLUE FLAG','Faster car approaching. Follow race control.');
  if(s.pit_status>0)return makeFlag('pit','#559aff','PIT LANE','Observe the pit-lane speed limit.',positive(s.pit_speed_limit_kph)?`${s.pit_speed_limit_kph} km/h LIMIT`:'');
  if(p.startsWith('formation'))return makeFlag('formation','#559aff','FORMATION LAP','Build temperature. Hold position.');
  // Unknown phases/flags must never be silently advertised as a clear track.
  if((!p&&!f)||(p&&!['green','racing'].includes(p))||(f&&!['none','green'].includes(f)))return makeFlag('unknown','#91a6b8','RACE CONTROL','Status unavailable. Follow in-game race control.');
  return makeFlag('green','#49d17d','GREEN FLAG','Track clear. Racing.');
}
export function normalizeState(s,{ageMs=0,transport='live',now=Date.now()}={}) {
  if(!s||typeof s!=='object')s={};
  const connected=s.connected===true, fresh=connected&&s.telemetry_stale!==true&&ageMs<3500;
  const drivers=Array.isArray(s.drivers)?s.drivers:[];
  const player=drivers.find(d=>d.car_idx===s.player_car_index)||{};
  const active=drivers.filter(d=>d.car_idx!==s.player_car_index&&d.active!==false&&![4,5,6].includes(d.result_status));
  const car=d=>d?{...d,name:String(d.name||'Unknown driver').slice(0,80),gap:finite(d.gap_to_player_s),position:positive(d.position),number:positive(d.race_number),last:positive(d.last_lap_ms),best:positive(d.best_lap_ms),compound:String(d.tyre_compound||'UNKNOWN'),tyreAge:finite(d.tyre_age)}:null;
  // gap_to_player_s is negative ahead and positive behind in the UDP adapter.
  const ahead=active.filter(d=>finite(d.gap_to_player_s)!==null&&d.gap_to_player_s<0).sort((a,b)=>b.gap_to_player_s-a.gap_to_player_s)[0];
  const behind=active.filter(d=>finite(d.gap_to_player_s)!==null&&d.gap_to_player_s>0).sort((a,b)=>a.gap_to_player_s-b.gap_to_player_s)[0];
  const recent=(Array.isArray(s.recent_laps)?s.recent_laps:[]).filter(l=>positive(l.lap_time_ms)&&l.valid!==false).slice(-6);
  const times=recent.map(l=>l.lap_time_ms),average=times.length?times.reduce((a,b)=>a+b,0)/times.length:null;
  const best=positive(player.best_lap_ms), delta=finite(s.live_delta_s);
  const reference=String(s.live_delta_reference||'Reference');
  const pb=reference.match(/^PB\s+(\d+):(\d{2})\.(\d{3})$/i);
  const referenceMs=pb?(Number(pb[1])*60+Number(pb[2]))*1000+Number(pb[3]):/session best/i.test(reference)?best:null;
  const fuelUsage=recent.map(l=>finite(l.fuel_start_kg)!==null&&finite(l.fuel_end_kg)!==null?l.fuel_start_kg-l.fuel_end_kg:null).filter(v=>v>0&&v<20);
  const burn=fuelUsage.length?fuelUsage.reduce((a,b)=>a+b,0)/fuelUsage.length:null;
  const recommendation=s.strategy?.available?s.strategy?.recommended:null;
  const nextStop=Array.isArray(recommendation?.box_laps)?recommendation.box_laps.find(l=>finite(l)!==null&&l>=s.current_lap):null;
  const array=a=>Array.from({length:4},(_,i)=>finite(a?.[i]));
  const freshGroup=id=>transport==='demo'||(finite(s.packet_group_freshness?.[id])!==null&&(now/1000-s.packet_group_freshness[id])<5);
  const carData=fresh&&freshGroup('6'),statusData=fresh&&freshGroup('7'),damageData=fresh&&freshGroup('10');
  const flag=fresh&&(!freshGroup('1')||!freshGroup('7'))?makeFlag('unknown','#91a6b8','RACE CONTROL UNAVAILABLE','Follow in-game race control. Awaiting current flag data.'):raceFlag(s,{fresh});
  return {fresh,transport,paused:!!s.game_paused,flag,track:String(s.track_name||'Waiting for session'),session:String(s.session_type||'No session'),
    lap:positive(s.current_lap),total:positive(s.total_laps),position:positive(s.player_position),field:positive(s.active_cars)||drivers.length||null,player:car(player),ahead:car(ahead),behind:car(behind),
    drivers:drivers.filter(d=>d.active!==false||d.car_idx===s.player_car_index).sort((a,b)=>(a.position||999)-(b.position||999)).map(car),
    current:positive(s.current_lap_time_ms),last:positive(s.last_lap_ms),best,delta,reference,invalid:!!s.current_lap_invalid,sector:finite(s.sector),
    predicted:referenceMs&&delta!==null?referenceMs+delta*1000:null,
    speed:carData?finite(s.speed_kph):null,gear:carData?finite(s.gear):null,rpm:carData?finite(s.engine_rpm):null,
    fuel:statusData?finite(s.fuel_kg):null,fuelMargin:statusData?finite(s.fuel_laps_delta):null,ers:statusData?finite(s.ers_pct):null,burn,
    aero:statusData&&s.regulations_2026?(s.active_aero_mode?'STRAIGHT':'CORNER'):null,ersMode:statusData?finite(s.ers_mode):null,
    compound:statusData?String(s.tyre?.compound||'UNKNOWN'):'UNKNOWN',tyreAge:statusData?finite(s.tyre?.age_laps):null,temps:carData?array(s.tyre?.surface_temps_c):[null,null,null,null],wear:damageData?array(s.tyre?.wear):[null,null,null,null],
    penalties:finite(s.penalties_s),warnings:finite(s.corner_cutting_warnings),nextStop:positive(nextStop),nextCompound:recommendation?.compounds?.[1]||null,
    plan:String(recommendation?.instruction||s.strategy?.reason||'Waiting for strategy'),recent,average,spread:times.length>1?Math.max(...times)-Math.min(...times):null,
    radio:String((Array.isArray(s.radio_log)?s.radio_log:[]).filter(r=>r.role==='engineer').at(-1)?.text||''),air:finite(s.air_temp_c),trackTemp:finite(s.track_temp_c)};
}
export function demoState(scenario='green',tick=0) {
  const t=tick%20, state={connected:true,source_mode:'demo',track_name:'Silverstone',session_type:'Race',current_lap:18,total_laps:52,player_car_index:3,player_position:5,active_cars:22,current_lap_time_ms:76842+t*500,last_lap_ms:88650,live_delta_s:-.238-t*.001,live_delta_reference:'Session best',sector:2,speed_kph:284+Math.round(Math.sin(t/3)*8),gear:6,engine_rpm:11420,regulations_2026:true,active_aero_mode:1,ers_mode:1,fuel_kg:46.2,fuel_laps_delta:1.3,ers_pct:68,air_temp_c:21,track_temp_c:34,penalties_s:0,corner_cutting_warnings:1,race_control_phase:'green',fia_flag:'green',tyre:{compound:'MEDIUM',age_laps:9,surface_temps_c:[92,93,94,93],wear:[18,21,16,17]},strategy:{available:true,recommended:{box_laps:[25],compounds:['MEDIUM','HARD'],instruction:'Box lap 25 for HARD.'}},radio_log:[{role:'engineer',text:'Pace is good. Piastri is 0.8 ahead. Keep the pressure on.'}],recent_laps:[88910,88680,88570,88780,88660,88650].map((time,i)=>({lap_num:12+i,lap_time_ms:time,valid:true,fuel_start_kg:60-i*1.31,fuel_end_kg:60-(i+1)*1.31})),drivers:[
    [2,'NORRIS',4,-4.281,88218,'MEDIUM',10],[3,'LECLERC',16,-2.406,88430,'HARD',15],[4,'PIASTRI',81,-.842+t*.00068,88770,'HARD',14],[5,'RUSSELL',63,0,88650,'MEDIUM',9],[6,'HAMILTON',44,1.267-t*.00045,88570,'MEDIUM',11],[7,'ALONSO',14,3.819,89012,'MEDIUM',12],[8,'SAINZ',55,6.110,89110,'HARD',14]
  ].map(([position,name,race_number,gap_to_player_s,last_lap_ms,tyre_compound,tyre_age],car_idx)=>({position,name,race_number,gap_to_player_s,last_lap_ms,tyre_compound,tyre_age,car_idx,active:true,best_lap_ms:car_idx===3?88412:last_lap_ms}))};
  if(scenario==='stale'){state.connected=false;state.telemetry_stale=true}
  else if(scenario==='yellow'||scenario==='blue')state.fia_flag=scenario;
  else if(scenario==='double-yellow')state.fia_flag='double_yellow';
  else if(scenario==='red'){state.race_control_phase='red_flag';state.speed_kph=0;state.gear=0}
  else if(scenario==='sc'||scenario==='vsc'){state.race_control_phase=scenario==='sc'?'safety_car':'vsc';state.safety_car_delta_valid=true;state.safety_car_delta_s=.85;state.speed_kph=130}
  else if(scenario==='penalty')state.unserved_drive_through_penalties=1;
  else if(scenario==='pit'){state.pit_status=2;state.pit_speed_limit_kph=80;state.speed_kph=79;state.gear=2}
  else if(scenario==='finish'){state.fia_flag='chequered';state.current_lap=52;state.speed_kph=0;state.gear=0}
  return state;
}
