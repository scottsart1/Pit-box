import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.UiAutomation;
import android.graphics.Rect;
import android.os.HandlerThread;
import android.os.Looper;
import android.util.Xml;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import java.io.StringWriter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import org.xmlpull.v1.XmlSerializer;

/** Read-only emulator QA dump, including native popups outside the active window. */
public final class AndroidUiHierarchy {
    private static String text(CharSequence value) { return value == null ? "" : value.toString(); }

    private static void attr(XmlSerializer xml, String key, Object value) throws Exception {
        xml.attribute("", key, String.valueOf(value));
    }

    private static void visit(XmlSerializer xml, AccessibilityNodeInfo node, int index, int depth) throws Exception {
        if (node == null) return;
        if (depth > 100) throw new IllegalStateException("Accessibility hierarchy exceeds safe dump depth");
        String pkg = text(node.getPackageName());
        if (pkg.equals("com.yourpitbox.app") || pkg.equals("com.yourpitbox.app.qa")) {
            throw new IllegalStateException("Refusing a production or physical-QA application window");
        }
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        xml.startTag("", "node");
        attr(xml, "index", index);
        attr(xml, "text", text(node.getText()));
        attr(xml, "resource-id", text(node.getViewIdResourceName()));
        attr(xml, "class", text(node.getClassName()));
        attr(xml, "package", pkg);
        attr(xml, "content-desc", text(node.getContentDescription()));
        attr(xml, "hint", text(node.getHintText()));
        attr(xml, "enabled", node.isEnabled());
        attr(xml, "visible-to-user", node.isVisibleToUser());
        attr(xml, "clickable", node.isClickable());
        attr(xml, "scrollable", node.isScrollable());
        attr(xml, "focused", node.isFocused());
        attr(xml, "checked", node.isChecked());
        attr(xml, "selected", node.isSelected());
        attr(xml, "bounds", "[" + bounds.left + "," + bounds.top + "][" + bounds.right + "," + bounds.bottom + "]");
        for (int child = 0; child < node.getChildCount(); child++) visit(xml, node.getChild(child), child, depth + 1);
        xml.endTag("", "node");
    }

    public static void main(String[] args) throws Exception {
        String qemu = (String) Class.forName("android.os.SystemProperties").getMethod("get", String.class)
            .invoke(null, "ro.kernel.qemu");
        if (!"1".equals(qemu)) throw new IllegalStateException("Hierarchy QA requires an emulator");
        HandlerThread thread = new HandlerThread("pitbox-qa-hierarchy");
        thread.start();
        UiAutomation ui = null;
        boolean connected = false;
        try {
            Class<?> connectionType = Class.forName("android.app.IUiAutomationConnection");
            Object connection = Class.forName("android.app.UiAutomationConnection").getConstructor().newInstance();
            // The two-argument constructor is public in AOSP's shell API. The
            // display-id constructor is private on API36; this test uses display0.
            ui = (UiAutomation) UiAutomation.class.getConstructor(Looper.class, connectionType)
                .newInstance(thread.getLooper(), connection);
            UiAutomation.class.getMethod("connect", int.class).invoke(ui, UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES);
            connected = true;
            AccessibilityServiceInfo service = ui.getServiceInfo();
            service.flags |= AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
                | AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
                | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
            ui.setServiceInfo(service);
            List<AccessibilityWindowInfo> windows = new ArrayList<>();
            long deadline = System.nanoTime() + 5_000_000_000L;
            do {
                windows.clear();
                for (AccessibilityWindowInfo window : ui.getWindows()) {
                    if (window.getDisplayId() == 0 && window.getRoot() != null) windows.add(window);
                }
                if (!windows.isEmpty()) break;
                Thread.sleep(100);
            } while (System.nanoTime() < deadline);
            if (windows.isEmpty()) throw new IllegalStateException("No interactive window roots on emulator display0");
            windows.sort(Comparator.comparingInt(AccessibilityWindowInfo::getLayer).reversed());
            StringWriter output = new StringWriter();
            XmlSerializer xml = Xml.newSerializer();
            xml.setOutput(output);
            xml.startDocument("UTF-8", true);
            xml.startTag("", "hierarchy");
            attr(xml, "display-id", 0);
            for (AccessibilityWindowInfo window : windows) {
                xml.startTag("", "window");
                attr(xml, "id", window.getId());
                attr(xml, "layer", window.getLayer());
                attr(xml, "active", window.isActive());
                attr(xml, "focused", window.isFocused());
                attr(xml, "title", text(window.getTitle()));
                visit(xml, window.getRoot(), 0, 0);
                xml.endTag("", "window");
            }
            xml.endTag("", "hierarchy");
            xml.endDocument();
            System.out.println(output);
        } finally {
            if (connected) UiAutomation.class.getMethod("disconnect").invoke(ui);
            thread.quitSafely();
        }
        // app_process can retain Binder worker threads after main returns.
        // This dedicated read-only process has completed and emitted its XML.
        System.exit(0);
    }
}
