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

function findPythonW() {
    var here = scriptFolder();
    var candidates = [];
    // a pythonw.exe sitting next to the scripts wins - lets the whole folder be
    // copied somewhere with its own interpreter
    candidates.push(here.fsName + "\\pythonw.exe");
    var envHome = "";
    try { envHome = $.getenv("LOCALAPPDATA") || ""; } catch (e) {}
    if (envHome) {
        var vers = ["313", "312", "311", "310", "39"];
        for (var i = 0; i < vers.length; i++) {
            candidates.push(envHome + "\\Programs\\Python\\Python" + vers[i] + "\\pythonw.exe");
        }
    }
    candidates.push("C:\\Python313\\pythonw.exe");
    candidates.push("C:\\Python312\\pythonw.exe");
    candidates.push("C:\\Python311\\pythonw.exe");
    for (var k = 0; k < candidates.length; k++) {
        var f = new File(candidates[k]);
        if (f.exists) return f.fsName;
    }
    return null;
}

(function () {
    var GUI = scriptFolder().fsName + "\\rimstudio_gui.py";
    var gui = new File(GUI);
    if (!gui.exists) { alert("RimStudio\n\nNo rimstudio_gui.py beside:\n" + $.fileName); return; }
    var PYW = findPythonW();
    if (!PYW) {
        alert("RimStudio\n\nCould not find pythonw.exe.\n\n" +
              "Put a copy beside this script, or install Python for the current user.");
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
