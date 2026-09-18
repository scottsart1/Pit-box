/* Share the existing application socket with the lazily loaded dashboard. */
(()=>{
  const frame=document.getElementById('driverDashboardFrame');
  if(!frame)return;
  let latest=null,ready=false;
  const visible=()=>!frame.closest('.page').hidden;
  const send=()=>{if(ready&&latest&&visible())frame.contentWindow.postMessage({type:'pitbox:driver-state',state:latest},location.origin)};
  const open=()=>{if(!visible())return;if(!frame.hasAttribute('src'))frame.src='/static/driver-dashboard/index.html?mode=embedded';send()};
  window.addEventListener('pitwall:state',e=>{latest=e.detail;send()});
  window.addEventListener('message',e=>{if(e.origin===location.origin&&e.source===frame.contentWindow&&e.data?.type==='pitbox:driver-ready'){ready=true;send()}});
  window.addEventListener('pitwall:pagechange',open);
  open();
})();
