# Third-party notices

Display Manager Pro's original code is licensed separately under AGPL-3.0-or-later. The third-party components below retain their own licenses. This notice describes the current macOS packaging configuration; verify versions and bundled files against each release build.

## PySide6, Qt for Python, and Qt

The app uses PySide6, PySide6-Essentials, PySide6-Addons, and shiboken6 **6.11.0**. The current build uses the Community Edition under the **LGPLv3** licensing option. The packaged app dynamically includes Qt libraries; it does not statically link Qt.

The PySide6 distribution can also be offered under GPL or commercial terms. This app's use is under the LGPLv3 option, not the GPL option. Qt and PySide6 remain separately licensed components; this does not place Display Manager Pro's original code under LGPL.

- Qt for Python licensing overview: <https://doc.qt.io/qtforpython-6/licenses.html>
- GNU LGPL version 3 text: <https://www.gnu.org/licenses/lgpl-3.0.txt>
- Qt licensing overview: <https://www.qt.io/licensing>

The bundle includes the Qt libraries and modules selected by PyInstaller for this app. Their individual copyright and third-party attribution notices are maintained by Qt; consult the Qt for Python licensing page for component-level notices and source availability.

## PyInstaller

The app is packaged with PyInstaller **6.19.0**. PyInstaller is licensed under GPLv2-or-later with a special exception permitting use to build and distribute non-free programs. PyInstaller is a build tool; the application bundle does not include its development package as an application feature.

- PyInstaller license: <https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt>

## Python runtime and bundled libraries

The app bundle includes the Python runtime and the dynamically linked libraries selected by the build environment. Their own licenses and notices continue to apply. The precise transitive runtime contents may vary with the build environment; inspect the final `.app` bundle before each release and include any additional notices required by its contents.

## Source and replacement

The corresponding source for Display Manager Pro is available in this repository. Qt/PySide6 are dynamically bundled as separate libraries; the LGPL permits replacement/relinking subject to its terms. The app is ad-hoc code-signed during local packaging, so modifying a signed copy may require re-signing it before macOS will launch it.
