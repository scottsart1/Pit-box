package com.yourpitbox.nascar;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslCertificate;
import android.net.http.SslError;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebChromeClient;
import android.webkit.ValueCallback;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.net.InetAddress;
import java.security.MessageDigest;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Locale;

/** Thin, separate companion. Race calculations stay on the paired Windows host. */
public final class MainActivity extends Activity {
    private WebView web;
    private TextView connectionStatus;
    private SharedPreferences prefs;
    private String origin = "", pinnedFingerprint = "", accessToken = "";
    private TextToSpeech tts;
    private boolean ttsReady;
    private boolean foreground;
    private SpeechRecognizer recognizer;
    private byte[] pendingExport;
    private ValueCallback<Uri[]> pendingFile;
    private final int background = Color.rgb(9,14,19);
    private final int accent = Color.rgb(255,193,92);

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        prefs = getSharedPreferences("nascar_companion", MODE_PRIVATE);
        tts = new TextToSpeech(this, status -> {
            ttsReady = status == TextToSpeech.SUCCESS;
            if (ttsReady) { tts.setLanguage(Locale.US); tts.setSpeechRate(1.06f); }
        });
        if (getIntent().getData() != null) acceptInvitation(getIntent().getData());
        else if (!prefs.getString("origin", "").isEmpty()) {
            origin = prefs.getString("origin", "");
            accessToken = prefs.getString("token", "");
            pinnedFingerprint = prefs.getString("fingerprint", "");
            showWorkspace();
        } else showPairing("");
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (intent.getData() != null) acceptInvitation(intent.getData());
    }

    private TextView text(String value, int size) {
        TextView view = new TextView(this); view.setText(value); view.setTextColor(Color.rgb(231,240,246));
        view.setTextSize(size); view.setPadding(0,14,0,14); return view;
    }
    private Button button(String label) {
        Button button = new Button(this); button.setText(label);button.setTextColor(background);button.setBackgroundTintList(android.content.res.ColorStateList.valueOf(accent));return button;
    }
    private void showPairing(String error) {
        disposeWeb();
        LinearLayout panel = new LinearLayout(this);panel.setOrientation(LinearLayout.VERTICAL);panel.setPadding(32,38,32,32);panel.setBackgroundColor(background);
        panel.addView(text("YOURPITBOX · NASCAR",16));
        panel.addView(text("Your crew chief,\non your tablet.",30));
        panel.addView(text("Start YourPitBox NASCAR on your Windows PC. In Connection, choose Show tablet connection. Scan its QR code with your tablet camera, or paste the invitation below.",16));
        panel.addView(text("The NASCAR companion has separate storage from the F1 app. Race data comes from the paired PC; it can use a calibrated game HUD or observations you enter.",14));
        if (!error.isEmpty()) {TextView warning=text(error,14);warning.setTextColor(accent);panel.addView(warning);}
        EditText invitation = new EditText(this);invitation.setHint("Paste yourpitbox-nascar://pair… invitation");invitation.setTextColor(Color.WHITE);invitation.setHintTextColor(Color.GRAY);invitation.setMinLines(2);invitation.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);panel.addView(invitation);
        Button connect=button("Connect to my NASCAR workspace");connect.setOnClickListener(v->acceptInvitation(Uri.parse(invitation.getText().toString().trim())));panel.addView(connect);
        if (!origin.isEmpty()) {Button back=button("Return to saved connection");back.setOnClickListener(v->showWorkspace());panel.addView(back);}
        ScrollView scroll = new ScrollView(this);scroll.addView(panel);setContentView(scroll);applyInsets(scroll);
    }

    private void acceptInvitation(Uri uri) {
        try {
            if (!"yourpitbox-nascar".equals(uri.getScheme()) || !"pair".equals(uri.getHost())) throw new IllegalArgumentException();
            String host=uri.getQueryParameter("host"), portString=uri.getQueryParameter("port");
            String token=uri.getQueryParameter("token"), fingerprint=uri.getQueryParameter("fingerprint");
            if (host==null || !host.matches("(?:[0-9]{1,3}\\.){3}[0-9]{1,3}")) throw new IllegalArgumentException();
            InetAddress address=InetAddress.getByName(host);
            if (!address.isSiteLocalAddress() && !address.isLoopbackAddress()) throw new IllegalArgumentException();
            int port=Integer.parseInt(portString);
            if (port<1 || port>65535 || token==null || !token.matches("[A-Za-z0-9_-]{20,100}") || fingerprint==null || !fingerprint.matches("[a-fA-F0-9]{64}")) throw new IllegalArgumentException();
            origin="https://"+host+":"+port;
            accessToken=token;pinnedFingerprint=fingerprint.toLowerCase(Locale.ROOT);
            prefs.edit().putString("origin",origin).putString("token",accessToken).putString("fingerprint",pinnedFingerprint).apply();
            showWorkspace();
        } catch(Exception ex) { showPairing("That invitation is incomplete. Copy or scan a fresh code from Connection on the PC."); }
    }

    private void applyInsets(View root) {
        root.setOnApplyWindowInsetsListener((view,insets)->{
            if(android.os.Build.VERSION.SDK_INT>=30){
                android.graphics.Insets bars=insets.getInsets(android.view.WindowInsets.Type.systemBars());
                view.setPadding(bars.left,bars.top,bars.right,bars.bottom);
            }else view.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());
            return insets;
        });
    }

    private boolean trustedPage() { return web!=null && web.getUrl()!=null && sameOrigin(Uri.parse(web.getUrl())); }
    private boolean sameOrigin(Uri uri) {
        Uri allowed=Uri.parse(origin);
        return "https".equals(uri.getScheme()) && allowed.getHost()!=null && allowed.getHost().equals(uri.getHost()) && allowed.getPort()==uri.getPort();
    }
    private void disposeWeb() {
        if(tts!=null)tts.stop();
        if(recognizer!=null){recognizer.destroy();recognizer=null;}
        if(pendingFile!=null){pendingFile.onReceiveValue(null);pendingFile=null;}
        if(web!=null){web.removeJavascriptInterface("NascarNative");web.stopLoading();web.destroy();web=null;}
    }
    private void showWorkspace() {
        disposeWeb();
        LinearLayout panel=new LinearLayout(this);panel.setOrientation(LinearLayout.VERTICAL);panel.setBackgroundColor(background);
        LinearLayout bar=new LinearLayout(this);bar.setPadding(12,0,12,0);
        connectionStatus=text("NASCAR · Connecting to your PC…",12);bar.addView(connectionStatus,new LinearLayout.LayoutParams(0,LinearLayout.LayoutParams.WRAP_CONTENT,1));
        Button settings=new Button(this);settings.setText("Connection");settings.setTextSize(11);settings.setTextColor(accent);settings.setBackgroundColor(background);settings.setOnClickListener(v->showPairing(""));bar.addView(settings);
        Button reload=new Button(this);reload.setText("Reload");reload.setTextSize(11);reload.setTextColor(accent);reload.setBackgroundColor(background);reload.setOnClickListener(v->{if(web!=null)web.reload();});bar.addView(reload);panel.addView(bar);
        web=new WebView(this);web.setBackgroundColor(background);
        WebSettings s=web.getSettings();s.setJavaScriptEnabled(true);s.setDomStorageEnabled(true);s.setAllowFileAccess(false);s.setAllowContentAccess(false);s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);s.setMediaPlaybackRequiresUserGesture(true);
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        web.addJavascriptInterface(new Bridge(),"NascarNative");
        web.setWebChromeClient(new WebChromeClient(){
            @Override public boolean onShowFileChooser(WebView view,ValueCallback<Uri[]> callback,FileChooserParams params){
                if(!trustedPage())return false;
                if(pendingFile!=null)pendingFile.onReceiveValue(null);
                pendingFile=callback;
                Intent pick=new Intent(Intent.ACTION_OPEN_DOCUMENT);pick.addCategory(Intent.CATEGORY_OPENABLE);pick.setType("*/*");
                pick.putExtra(Intent.EXTRA_MIME_TYPES,new String[]{"text/csv","text/plain","application/csv","application/vnd.ms-excel"});
                startActivityForResult(pick,22);return true;
            }
        });
        web.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request) {
                if(sameOrigin(request.getUrl())) return false;
                if("yourpitbox-nascar".equals(request.getUrl().getScheme())) acceptInvitation(request.getUrl());
                return true;
            }
            @Override public void onReceivedSslError(WebView view,SslErrorHandler handler,SslError error) {
                try {
                    if(!sameOrigin(Uri.parse(error.getUrl()))) throw new IllegalArgumentException();
                    Bundle bundle=SslCertificate.saveState(error.getCertificate());
                    byte[] bytes=bundle.getByteArray("x509-certificate");
                    X509Certificate cert=(X509Certificate)CertificateFactory.getInstance("X.509").generateCertificate(new ByteArrayInputStream(bytes));
                    cert.checkValidity();
                    byte[] digest=MessageDigest.getInstance("SHA-256").digest(cert.getEncoded());
                    StringBuilder hex=new StringBuilder();for(byte b:digest)hex.append(String.format(Locale.ROOT,"%02x",b&255));
                    if(!MessageDigest.isEqual(hex.toString().getBytes(java.nio.charset.StandardCharsets.US_ASCII),pinnedFingerprint.getBytes(java.nio.charset.StandardCharsets.US_ASCII))) throw new IllegalArgumentException();
                    handler.proceed();
                } catch(Exception ex) {handler.cancel();connectionStatus.setText("Certificate changed. Scan a fresh PC invitation.");}
            }
            @Override public void onReceivedError(WebView view,WebResourceRequest request,WebResourceError error){if(request.isForMainFrame())connectionStatus.setText("PC unavailable. Check local Wi-Fi and the desktop app, then Reload.");}
            @Override public void onPageFinished(WebView view,String url){if(sameOrigin(Uri.parse(url)))connectionStatus.setText("NASCAR · "+Uri.parse(origin).getHost());}
        });
        panel.addView(web,new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,0,1));setContentView(panel);applyInsets(panel);
        web.loadUrl(origin+"/#token="+Uri.encode(accessToken));
    }

    public final class Bridge {
        @JavascriptInterface public void speak(String message) { runOnUiThread(()->{if(foreground&&trustedPage()&&ttsReady&&message.length()<=8000)tts.speak(message,TextToSpeech.QUEUE_FLUSH,null,"nascar-radio");}); }
        @JavascriptInterface public void stopSpeech(){runOnUiThread(()->{if(tts!=null)tts.stop();});}
        @JavascriptInterface public void listen(){runOnUiThread(()->{if(trustedPage())startListening();});}
        @JavascriptInterface public void saveFile(String filename,String mime,String text){runOnUiThread(()->{
            if(!foreground||!trustedPage()||text.length()>4_000_000)return;
            pendingExport=text.getBytes(java.nio.charset.StandardCharsets.UTF_8);
            Intent save=new Intent(Intent.ACTION_CREATE_DOCUMENT);save.addCategory(Intent.CATEGORY_OPENABLE);
            save.setType("text/csv".equals(mime)?"text/csv":"application/json");
            save.putExtra(Intent.EXTRA_TITLE,filename.replaceAll("[^a-zA-Z0-9._-]","_"));startActivityForResult(save,21);
        });}
    }
    private void jsResult(String function,String value) {if(trustedPage())web.evaluateJavascript("window."+function+"("+JSONObject.quote(value)+")",null);}
    private void startListening() {
        if(!foreground||!trustedPage())return;
        if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO},12);return;}
        if(!SpeechRecognizer.isRecognitionAvailable(this)){jsResult("nascarSpeechError","No speech recognition service is installed. Type your question instead.");return;}
        if(tts!=null)tts.stop();
        if(recognizer!=null)recognizer.destroy();
        recognizer=SpeechRecognizer.createSpeechRecognizer(this);
        recognizer.setRecognitionListener(new RecognitionListener(){
            public void onReadyForSpeech(Bundle p){} public void onBeginningOfSpeech(){} public void onRmsChanged(float r){} public void onBufferReceived(byte[] b){} public void onEndOfSpeech(){} public void onPartialResults(Bundle b){} public void onEvent(int t,Bundle b){}
            public void onError(int code){jsResult("nascarSpeechError","Speech wasn't captured ("+code+"). Tap the microphone to try again, or type your question.");}
            public void onResults(Bundle results){ArrayList<String> words=results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);if(words!=null&&!words.isEmpty())jsResult("nascarSpeechResult",words.get(0));}
        });
        Intent request=new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);request.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);request.putExtra(RecognizerIntent.EXTRA_LANGUAGE,"en-US");request.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS,false);recognizer.startListening(request);
    }
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] results){super.onRequestPermissionsResult(request,permissions,results);if(request==12){if(results.length>0&&results[0]==PackageManager.PERMISSION_GRANTED)startListening();else jsResult("nascarSpeechError","Microphone permission is off. You can still type your question.");}}
    @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);if(request==22&&pendingFile!=null){pendingFile.onReceiveValue(result==RESULT_OK&&data!=null&&data.getData()!=null?new Uri[]{data.getData()}:null);pendingFile=null;}if(request==21){try{if(result==RESULT_OK&&data!=null&&data.getData()!=null&&pendingExport!=null){try(java.io.OutputStream stream=getContentResolver().openOutputStream(data.getData())){stream.write(pendingExport);}}}catch(Exception ex){android.widget.Toast.makeText(this,"The export could not be saved.",android.widget.Toast.LENGTH_LONG).show();}finally{pendingExport=null;}}}
    @Override protected void onResume(){super.onResume();foreground=true;if(web!=null)web.onResume();}
    @Override protected void onPause(){foreground=false;if(recognizer!=null)recognizer.cancel();if(tts!=null)tts.stop();if(web!=null)web.onPause();super.onPause();}
    @Override protected void onDestroy(){disposeWeb();if(tts!=null)tts.shutdown();super.onDestroy();}
}
