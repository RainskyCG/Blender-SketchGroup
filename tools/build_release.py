"""Build a validated extension and a clean GitHub source snapshot (Python 3.11+)."""
import argparse
import ast
import hashlib
import shutil
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path

MODULES = ('__init__ animation compensation coordinates draw_dependencies explode focus '
           'instances interaction keymaps markers origins outliner outside preview safety '
           'selection_state session_io silhouette source_display source_edit structure ui viewport').split()
DOCS = ['README.md', 'LICENSE', 'CONTRIBUTING.md', 'blender_manifest.toml',
        'docs/USER_GUIDE.zh-CN.md', 'docs/BUILD.md', 'docs/RELEASE_NOTES.md',
        'docs/_assets/image-20261103-102921-ocxd07.gif', 'docs/images/03-edit-linked.png',
        'docs/images/preferences-general.jpg', 'docs/images/preferences-keymap.jpg']
ASSETS = ['assets/icons/' + name for name in ('github.png', 'github.svg', 'artstation.png',
          'artstation.svg', 'bilibili.png', 'bilibili.svg', 'LICENSE.txt', 'SOURCES.md')]
SOURCE_ONLY = ['tools/build_release.py', 'tools/smoke_test.py', '.gitignore',
               '.github/ISSUE_TEMPLATE/bug_report.md', '.github/PULL_REQUEST_TEMPLATE.md']


def copy_files(root, target, names):
    for name in names:
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = (args.output or root / 'release').resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((root / 'blender_manifest.toml').read_text(encoding='utf-8'))['version']
    names = [name + '.py' for name in MODULES] + DOCS + ASSETS
    for name in names + SOURCE_ONLY:
        if not (root / name).is_file():raise FileNotFoundError(name)
        if name.endswith('.py'):ast.parse((root / name).read_text(encoding='utf-8'),filename=name)
    archive = output / f'SketchGroup-v{version}.zip'
    source = output / f'SketchGroup-v{version}-source'
    if archive.exists() or source.exists():raise FileExistsError('Choose an empty output directory')
    options = {}
    if __import__('sys').platform == 'win32':
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options['startupinfo'] = startup
    def command(*items):
        subprocess.run([str(args.blender), '--factory-startup', *map(str,items)], check=True, **options)
    with tempfile.TemporaryDirectory(prefix='stage-', dir=output) as temporary:
        stage = Path(temporary).resolve()
        assert stage.parent == output
        copy_files(root, stage, names)
        command('--command', 'extension', 'validate', stage)
        command('--command', 'extension', 'build', '--source-dir', stage, '--output-filepath', archive)
        command('--command', 'extension', 'validate', archive)
        with zipfile.ZipFile(archive) as package:
            contents = set(package.namelist())
            assert contents == set(names), (contents-set(names), set(names)-contents)
            assert package.testzip() is None
        command('--background', '--python-exit-code', '1', '--python', root/'tools/smoke_test.py', '--', archive)
    copy_files(root, source, names + SOURCE_ONLY)
    source_archive = output / f'SketchGroup-v{version}-source.zip'
    with zipfile.ZipFile(source_archive, 'w', zipfile.ZIP_DEFLATED) as package:
        for name in sorted(names + SOURCE_ONLY):package.write(source/name,name)
    lines = [hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name for path in (archive,source_archive)]
    (output/'SHA256SUMS.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    shutil.copy2(root/'docs/RELEASE_NOTES.md',output/'RELEASE_NOTES.md')
    print('\n'.join(lines))
    print('GitHub source:', source)


if __name__ == '__main__':main()
