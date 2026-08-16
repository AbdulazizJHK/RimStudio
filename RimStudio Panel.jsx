/**
 * RimStudio Panel.jsx  -  opens the RimStudio window.
 *
 * File > Scripts > Browse... > this file. Put it in
 *   C:\Program Files\Adobe\Adobe Photoshop 2026\Presets\Scripts\
 * (elevated copy + restart) to get it in the Scripts menu permanently.
 *
 * It only launches the window and returns immediately, so Photoshop is never
 * blocked. The window talks back to Photoshop on its own through the same COM
 * bridge - press "Pull from Photoshop" in the window with your cut-out layer
 * selected.
 *
 * pythonw.exe, not python.exe: pythonw has no console, so no black terminal
 * window sits behind the panel for the whole session.
 */
/* Nothing is hardcoded: the script finds its own folder, and pythonw is looked
 * up rather than pinned to one install. A pinned path breaks the moment the
 * folder is renamed, moved, or opened on another machine - and it breaks
 * silently, which is worse. */
function scriptFolder() {
    try { return File($.fileName).parent; } catch (e) { return Folder.current; }
}

/* The interpreter install.ps1 built, if it ran. A recorded path beats a search:
 * it is the one Python we know has numpy and Pillow in it, and the machine may
 * well have three others that do not. */
function pythonFromConfig() {
    try {
        var appData = $.getenv("APPDATA");
        if (!appData) return null;
        var cfg = new File(appData + "\\RimStudio\\config.txt");
        if (!cfg.exists) return null;
        cfg.encoding = "UTF-8";           // paths may hold non-ASCII characters
        cfg.open("r");
        var text = cfg.read();
        cfg.close();
        var lines = text.split(/[\r\n]+/);
        for (var i = 0; i < lines.length; i++) {
            var m = lines[i].match(/^\s*pythonw\s*=\s*(.+?)\s*$/);
            if (m && new File(m[1]).exists) return m[1];
        }
    } catch (e) {}
    return null;
}

function findPythonW() {
    var here = scriptFolder();
    var candidates = [];
    // what the installer recorded
    var cfg = pythonFromConfig();
    if (cfg) candidates.push(cfg);
    // a virtual environment made by hand, either beside the tool or where the
    // installer puts one
    candidates.push(here.fsName + "\\.venv\\Scripts\\pythonw.exe");
    candidates.push(here.fsName + "\\venv\\Scripts\\pythonw.exe");
    var local = "";
    try { local = $.getenv("LOCALAPPDATA") || ""; } catch (e) {}
    if (local) candidates.push(local + "\\RimStudio\\venv\\Scripts\\pythonw.exe");
    // a pythonw.exe sitting next to the scripts - lets the whole folder be
    // copied somewhere with its own interpreter
    candidates.push(here.fsName + "\\pythonw.exe");
    var vers = ["314", "313", "312", "311", "310", "39"];
    var i;
    if (local) {
        for (i = 0; i < vers.length; i++) {
            candidates.push(local + "\\Programs\\Python\\Python" + vers[i] + "\\pythonw.exe");
        }
    }
    var progs = "";
    try { progs = $.getenv("ProgramFiles") || ""; } catch (e2) {}
    if (progs) {
        for (i = 0; i < vers.length; i++) {
            candidates.push(progs + "\\Python" + vers[i] + "\\pythonw.exe");
        }
    }
    for (i = 0; i < vers.length; i++) candidates.push("C:\\Python" + vers[i] + "\\pythonw.exe");
    for (var k = 0; k < candidates.length; k++) {
        var f = new File(candidates[k]);
        if (f.exists) return f.fsName;
    }
    // last resort: the py launcher, which lives in Windows itself and knows
    // where every Python is. pyw is the no-console half of it.
    try {
        var win = $.getenv("WINDIR");
        if (win && new File(win + "\\pyw.exe").exists) return win + "\\pyw.exe";
    } catch (e3) {}
    return null;
}

(function () {
    var GUI = scriptFolder().fsName + "\\rimstudio_gui.py";
    var gui = new File(GUI);
    if (!gui.exists) { alert("RimStudio\n\nNo rimstudio_gui.py beside:\n" + $.fileName); return; }
    var PYW = findPythonW();
    if (!PYW) {
        alert("RimStudio\n\nCould not find Python.\n\n" +
              "Run install.ps1 from the RimStudio folder - it installs Python's\n" +
              "two dependencies and records where they are.\n\n" +
              "Or install Python 3.9+ from python.org for the current user.");
        return;
    }

    // "start" so app.system() returns at once instead of waiting for the window
    // to close - otherwise Photoshop sits frozen behind its own panel.
    var cmd = 'start "RimStudio" "' + PYW + '" "' + GUI + '"';
    var bat = new File(Folder.temp + "/rimstudio_launch.bat");
    bat.open("w");
    bat.writeln("@echo off");
    bat.writeln(cmd);
    bat.close();
    app.system('"' + bat.fsName + '"');
})();
"launched";
