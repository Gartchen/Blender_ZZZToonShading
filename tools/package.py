"""Build the installable ZIP from reviewed Git files. Run with Python 3.10+."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = subprocess.check_output(["git", "ls-files", "-z", "--", "zzz_toon"], cwd=root)
    names = sorted(raw.decode("utf8") for raw in files.split(b"\0") if raw)
    if "zzz_toon/__init__.py" not in names:
        raise SystemExit("No tracked add-on entry point found.")
    # Game meshes can also be serialized as JSON. Only the reviewed preset
    # directories belong in a public build, irrespective of filename suffix.
    roles = {"Remielle", "Velina", "Vivian"}
    globals_ = {"preview_data/index.json", "wardrobe_data/page.json",
                "wardrobe_data/slots.json", "wardrobe_data/studio-tile.png"}
    presets = {"geometry.json", "render_look.json", "dynamics.json", "face_outline.json", "attachments.json"}
    for name in names:
        path = Path(name).relative_to("zzz_toon")
        parts = path.parts
        allowed = (len(parts) == 1 and path.suffix == ".py") or path.as_posix() in globals_
        if len(parts) >= 3 and parts[1] in roles:
            allowed |= (parts[0] == "preview_data" and parts[2:] == ("preview.json",))
            allowed |= (parts[0] == "wardrobe_data" and len(parts) == 3 and parts[2] in presets)
            allowed |= (parts[0] == "wardrobe_data" and len(parts) == 4 and parts[2] == "Materials"
                        and path.suffix == ".json" and path.stem.startswith("MAT_"))
        if not allowed or (root / name).is_symlink():
            raise SystemExit("Review unexpected add-on file: " + name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w") as archive:
        for name in names:
            data = (root / name).read_bytes()
            if name.endswith((".json", ".py")):
                data = data.replace(b"\r\n", b"\n")
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    print(hashlib.sha256(args.output.read_bytes()).hexdigest(), args.output.name)


if __name__ == "__main__":
    main()
