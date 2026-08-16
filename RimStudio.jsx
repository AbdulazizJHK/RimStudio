/**
 * RimStudio.jsx  -  the File > Scripts > RimStudio menu entry.
 *
 * Installed in:
 *   C:\Program Files\Adobe\Adobe Photoshop <version>\Presets\Scripts\
 *
 * This is deliberately a STUB. It holds no logic of its own - it finds and runs
 * "RimStudio Panel.jsx" wherever the tool actually lives. Copying the real
 * script here instead would mean an elevated copy into Program Files after
 * every single change, and a stale copy the first time someone forgot.
 *
 * No hardcoded user path. It asks, in order:
 *   1. %APPDATA%\RimStudio\config.txt, written by install.ps1 - so the tool
 *      folder can be anywhere, including a cloned repo folder
 *   2. Documents\Photoshop Scripts, the usual home
 *   3. the folder this stub is sitting in, for a plain copy-everything install
 */
#target photoshop
// rimstudio-menu-stub — the installer looks for this exact line before copying
// this file into Program Files. A folder can hold more than one RimStudio.jsx
// (an old redirect left behind by a previous version, say), and installing the
// wrong one gives a menu entry that opens an alert instead of the tool.

(function () {
    var PANEL = "RimStudio Panel.jsx";
    var candidates = [];

    function add(folder) {
        if (folder) candidates.push(folder.replace(/[\\\/]+$/, "") + "\\" + PANEL);
    }

    // 1. the installer's config file: "tool=C:\somewhere\RimStudio"
    try {
        var appData = $.getenv("APPDATA");
        if (appData) {
            var cfg = new File(appData + "\\RimStudio\\config.txt");
            if (cfg.exists) {
                cfg.encoding = "UTF-8";   // paths may hold non-ASCII characters
                cfg.open("r");
                var text = cfg.read();
                cfg.close();
                var lines = text.split(/[\r\n]+/);
                for (var i = 0; i < lines.length; i++) {
                    var m = lines[i].match(/^\s*tool\s*=\s*(.+?)\s*$/);
                    if (m) add(m[1]);
                }
            }
        }
    } catch (e) {}

    // 2. where the README tells people to put it
    try { add(Folder.myDocuments.fsName + "\\Photoshop Scripts"); } catch (e2) {}
    try { add(Folder.myDocuments.fsName + "\\Photoshop Scripts\\RimStudio"); } catch (e3) {}

    // 3. beside this stub, if someone copied the whole folder into Presets
    try { add(File($.fileName).parent.fsName); } catch (e4) {}

    for (var k = 0; k < candidates.length; k++) {
        var f = new File(candidates[k]);
        if (f.exists) { $.evalFile(f); return; }
    }

    alert("RimStudio\n\nCould not find \"" + PANEL + "\".\n\nLooked in:\n" +
          candidates.join("\n") +
          "\n\nFix: run install.ps1 from the RimStudio folder - it records the " +
          "location for this menu entry.\nOr open it once with File > Scripts > Browse...");
})();
