import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from time import perf_counter as timer

def discover_local_packages_and_modules(project_root):
    package_names = [
        p.name
        for p in project_root.iterdir()
        if p.is_dir() and (p / "__init__.py").is_file()
    ]
    module_names = [
        p.stem
        for p in project_root.iterdir()
        if p.suffix == ".py" and p.name != "main.py"
    ]
    return package_names, module_names

def ensure_linux_or_exit():
    if sys.platform != "linux":
        print(f"[ERROR] Linux only. Current platform: {sys.platform}")
        sys.exit(1)

def ensure_tool_or_exit(tool_name):
    if shutil.which(tool_name) is None:
        print(f"[ERROR] Missing tool: {tool_name} (not in PATH)")
        sys.exit(1)

def write_apprun(appdir_path, executable_name):
    app_run_content = textwrap.dedent(
        f"""\
        #!/bin/sh
        set -eu
        APPDIR="${{APPDIR:-$(dirname "$(readlink -f "$0")")}}"
        export PATH="$APPDIR/usr/bin:$PATH"
        cd "$APPDIR/usr/bin"
        exec "./{executable_name}" "$@"
        """
    )
    apprun_path = appdir_path / "AppRun"
    apprun_path.write_text(app_run_content, encoding="utf-8")
    apprun_path.chmod(0o755)

def stage_desktop_and_icon_from_code(appdir_path, project_root, executable_basename, icon_basename="icon"):
    assets_dir = project_root / "assets"
    if not assets_dir.exists() or not assets_dir.is_dir():
        raise FileNotFoundError("assets/ folder not found")

    icon_source_png = assets_dir / f"{icon_basename}.png"
    if not icon_source_png.exists() or not icon_source_png.is_file():
        raise FileNotFoundError(f"Icon not found in assets/: expected {icon_basename}.png")

    shutil.copy2(icon_source_png, appdir_path / f"{icon_basename}.png")

    desktop_filename = f"{executable_basename}.desktop"
    desktop_path = appdir_path / desktop_filename

    desktop_file_content = textwrap.dedent(
        f"""\
        [Desktop Entry]
        Version=1.0
        Type=Application
        Name={executable_basename.replace("-", " ").title()}
        Comment=A simple puzzle maze game with fog.
        Exec={executable_basename}
        Icon={icon_basename}
        Categories=Game;
        Keywords=game;maze;fog;puzzle;
        Terminal=false
        StartupNotify=true
        """
    )
    desktop_path.write_text(desktop_file_content, encoding="utf-8")

    applications_dir = appdir_path / "usr" / "share" / "applications"
    applications_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(desktop_path, applications_dir / desktop_filename)

    icons_hicolor_base = appdir_path / "usr" / "share" / "icons" / "hicolor"
    icon_dest_128 = icons_hicolor_base / "128x128" / f"{icon_basename}.png"
    icon_dest_128.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_source_png, icon_dest_128)

    return {"desktop_name": desktop_filename, "exec_name": executable_basename, "icon_name": icon_basename}

def run_checked(command, **subprocess_kwargs):
    try:
        subprocess.run(command, check=True, **subprocess_kwargs)
    except FileNotFoundError:
        print(f"[ERROR] Command not found: {command[0]}")
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        joined = " ".join(map(str, command))
        print(f"[ERROR] Command failed ({exc.returncode}): {joined}")
        sys.exit(1)

def copy_licenses(project_root, appdir_path):
    licenses_src_dir = project_root / "data" / "licenses"
    licenses_dst_dir = appdir_path / "usr" / "share" / "licenses"

    if not licenses_src_dir.exists():
        print("  [INFO] data/licenses not found")
        return 0

    licenses_dst_dir.mkdir(parents=True, exist_ok=True)
    copied_count = 0

    for license_path in licenses_src_dir.iterdir():
        if license_path.is_file():
            shutil.copy2(license_path, licenses_dst_dir / license_path.name)
            copied_count += 1

    (licenses_dst_dir / "README.txt").write_text(
        "THIRD-PARTY LICENSES\n{}\n\n"
        "This directory contains license information for third-party libraries\n"
        "included in this application.\n\n"
        "{}\n\n".format("=" * 70, "=" * 70),
        encoding="utf-8",
    )

    return copied_count

def build_appimage(name):
    ensure_linux_or_exit()
    ensure_tool_or_exit("appimagetool")

    start_time = timer()
    project_root = Path.cwd()
    entrypoint_script = project_root / "main.py"

    application_name = name
    executable_name = application_name
    app_version = "1.0"

    if not entrypoint_script.exists():
        print("[ERROR] main.py not found in project root.")
        sys.exit(1)

    output_build_dir = project_root / "build"
    output_appimage_path = output_build_dir / f"{application_name}.AppImage"

    print("== APPIMAGE BUILD ==")
    print(f"Project     : {project_root}")
    print(f"App name    : {application_name}")
    print(f"Entrypoint  : {entrypoint_script}")
    print(f"Output file : {output_appimage_path}")
    print("====================")

    package_names, module_names = discover_local_packages_and_modules(project_root)
    filtered_modules = [m for m in module_names if m != "build"]

    print("[1/6] Prepare output")
    output_build_dir.mkdir(exist_ok=True)
    if output_appimage_path.exists():
        output_appimage_path.unlink()
        print(f"  Removed old file: {output_appimage_path.name}")

    print("[2/6] Freeze python app")
    if package_names:
        print(f"  Packages : {len(package_names)}")
    if filtered_modules:
        print(f"  Modules  : {len(filtered_modules)}")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        appdir_path = temp_path / f"{application_name}.AppDir"
        python_bin_dir = appdir_path / "usr" / "bin"
        python_bin_dir.mkdir(parents=True, exist_ok=True)

        setup_script_code = textwrap.dedent(
            f"""\
            import sys
            sys.path.insert(0, r"{project_root}")
            from cx_Freeze import setup, Executable

            setup(
                name="{application_name}",
                version="{app_version}",
                description="{application_name} Build",
                executables=[Executable(r"{entrypoint_script}", target_name="{executable_name}")],
                options={{
                    "build_exe": {{
                        "build_exe": r"{python_bin_dir}",
                        "packages": {package_names + filtered_modules!r},
                        "includes": [],
                        "include_files": [],
                    }}
                }}
            )
            """
        )

        setup_script_path = temp_path / "setup_cxfreeze.py"
        setup_script_path.write_text(setup_script_code, encoding="utf-8")

        print("  Freezing... (cx_Freeze)")
        run_checked(
            [sys.executable, str(setup_script_path), "build"],
            cwd=temp_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )

        frozen_executable_path = python_bin_dir / executable_name
        if not frozen_executable_path.exists():
            print(f"[ERROR] Frozen binary not found: {frozen_executable_path}")
            sys.exit(1)

        print("[3/6] Copy assets")
        asset_copy_count = 0
        for resource_dir_name in ["data", "assets"]:
            source_path = project_root / resource_dir_name
            destination_path = python_bin_dir / resource_dir_name

            if not source_path.exists():
                continue

            try:
                if source_path.is_dir():
                    shutil.copytree(
                        source_path,
                        destination_path,
                        ignore=shutil.ignore_patterns("__pycache__"),
                    )
                else:
                    destination_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_path, destination_path)

                asset_copy_count += 1
                print(f"  Copied: {resource_dir_name}")
            except Exception as exc:
                print(f"  [WARN] Failed to copy {resource_dir_name}: {exc}")

        if asset_copy_count == 0:
            print("  [INFO] No assets copied")

        print("[4/6] Copy licenses")
        license_copy_count = copy_licenses(project_root, appdir_path)
        print(f"  License files copied: {license_copy_count}")

        print("[5/6] Create .desktop and icon (code-generated)")
        metadata = stage_desktop_and_icon_from_code(
            appdir_path=appdir_path,
            project_root=project_root,
            executable_basename=executable_name,
            icon_basename="icon",
        )
        print(f"  Desktop : {metadata['desktop_name']}")
        print(f"  Exec    : {metadata['exec_name']}")
        print(f"  Icon    : {metadata['icon_name']}")

        print("[6/6] Build AppImage")
        write_apprun(appdir_path, executable_name)

        appimage_env = os.environ.copy()
        appimage_env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
        appimage_env["NO_APPSTREAM"] = "1"

        run_checked(
            ["appimagetool", str(appdir_path), str(output_appimage_path)],
            env=appimage_env,
        )

    final_size_mb = (
        output_appimage_path.stat().st_size / (1024 * 1024)
        if output_appimage_path.exists()
        else 0.0
    )

    elapsed = timer() - start_time
    print("====================")
    print("BUILD SUCCESS")
    print(f"File : {output_appimage_path}")
    print(f"Size : {final_size_mb:.2f} MB")
    print(f"Time : {elapsed:.2f}s")
    print("====================")

if __name__ == "__main__":
    build_appimage("mystic-fog-maze")
