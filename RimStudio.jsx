/**
 * RimStudio.jsx  -  the File > Scripts > RimStudio menu entry.
 *
 * Installed in:
 *   C:\Program Files\Adobe\Adobe Photoshop 2026\Presets\Scripts\
 *
 * This is deliberately a STUB. It holds no logic of its own - it finds and runs
 * "RimStudio Panel.jsx" in Documents\Photoshop Scripts, which is where the tool
 * actually lives and gets edited. Copying the real script here instead would
 * mean an elevated copy into Program Files after every single change, and a
 * stale copy the first time someone forgot.
 *
 * No hardcoded user path: Folder.myDocuments resolves per user, so this same
 * file works for any account and survives the Documents folder being
 * redirected or synced.
 */
#target photoshop

(function () {
    var candidates = [];
    try {
        candidates.push(Folder.myDocuments.fsName + "\\Photoshop Scripts\\RimStudio Panel.jsx");
    } catch (e) {}
    try {
        candidates.push(Folder.userData.parent.fsName + "\\..\\Documents\\Photoshop Scripts\\RimStudio Panel.jsx");
    } catch (e2) {}

    for (var i = 0; i < candidates.length; i++) {
        var f = new File(candidates[i]);
        if (f.exists) {
            $.evalFile(f);
            return;
        }
    }
    alert("RimStudio\n\nCould not find \"RimStudio Panel.jsx\".\n\nExpected it in:\n" +
          candidates.join("\n") +
          "\n\nIf the tool folder moved, run the panel once with\nFile > Scripts > Browse...");
})();
