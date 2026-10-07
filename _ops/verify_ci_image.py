#!/usr/bin/env python3
"""Verify a CI image archive, config/manifest linkage and embedded binary.

Docker versions differ in whether inspect.Id exposes the config digest or the
OCI manifest digest. Accept either only after proving the content-addressed
manifest points to the exact CI-recorded config and all layer hashes agree.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile


def sha(stream):
    h = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        h.update(chunk)
    return h.hexdigest()


def verify(directory, commit):
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        expected, file = line.split(maxsplit=1)
        file = file.lstrip("*")
        path = directory / file
        if path.resolve().parent != directory.resolve():
            raise ValueError("artifact checksum path leaves its directory")
        with path.open("rb") as stream:
            if sha(stream) != expected:
                raise ValueError(f"artifact checksum mismatch: {file}")
    info = json.loads((directory / "build-info.json").read_text())
    if info["commit"] != commit:
        raise ValueError("artifact source revision mismatch")
    config_id = (directory / "image-id.txt").read_text().strip()
    binary_sha = hashlib.sha256((directory / "gproxy").read_bytes()).hexdigest()
    archive = directory / "gproxy-upgrade-image.tar.gz"
    with tarfile.open(archive, "r:gz") as outer:
        index = json.load(outer.extractfile("index.json"))
        manifests = index["manifests"]
        if len(manifests) != 1:
            raise ValueError("expected one native platform manifest")
        manifest_id = manifests[0]["digest"]

        def read_blob(digest, size=None):
            kind, name = digest.split(":", 1)
            if kind != "sha256":
                raise ValueError("unsupported image digest kind")
            member = outer.getmember("blobs/sha256/" + name)
            if size is not None and member.size != size:
                raise ValueError("OCI blob length mismatch")
            raw = outer.extractfile(member).read()
            if hashlib.sha256(raw).hexdigest() != name:
                raise ValueError("OCI blob hash mismatch")
            return raw

        manifest = json.loads(read_blob(manifest_id, manifests[0]["size"]))
        if manifest["config"]["digest"] != config_id:
            raise ValueError("manifest does not point to the CI-recorded config")
        config = json.loads(read_blob(config_id, manifest["config"]["size"]))
        if config["architecture"] != "amd64" or config["os"] != "linux":
            raise ValueError("unexpected image platform")
        labels = config["config"]["Labels"]
        if labels.get("org.opencontainers.image.revision") != commit:
            raise ValueError("image revision label mismatch")
        embedded = []
        for layer in manifest["layers"]:
            if layer["mediaType"] != "application/vnd.oci.image.layer.v1.tar":
                raise ValueError("this artifact verifier expects uncompressed OCI layers")
            _, name = layer["digest"].split(":", 1)
            member = outer.getmember("blobs/sha256/" + name)
            if member.size != layer["size"] or sha(outer.extractfile(member)) != name:
                raise ValueError("OCI layer hash/length mismatch")
            with tarfile.open(fileobj=outer.extractfile(member), mode="r:") as inner:
                for entry in inner:
                    if entry.name.lstrip("./") == "usr/local/bin/gproxy":
                        embedded.append(sha(inner.extractfile(entry)))
        if embedded != [binary_sha]:
            raise ValueError("image binary differs from the CI binary artifact")
    subprocess.run(["docker", "load", "-i", str(archive)], check=True, capture_output=True)
    installed = json.loads(subprocess.run(["docker", "image", "inspect", info["image"]],
                                         check=True, text=True, capture_output=True).stdout)[0]
    if installed["Id"] not in (config_id, manifest_id):
        raise ValueError("loaded Docker image ID is neither the verified config nor manifest")
    if installed["RootFS"]["Layers"] != config["rootfs"]["diff_ids"]:
        raise ValueError("loaded image layers differ from the verified archive")
    if installed["Config"]["Labels"].get("org.opencontainers.image.revision") != commit:
        raise ValueError("loaded image source revision mismatch")
    result = {"passed": True, "commit": commit, "image": info["image"], "binary_sha256": binary_sha,
              "config_digest": config_id, "manifest_digest": manifest_id, "loaded_image_id": installed["Id"],
              "verified_layers": len(manifest["layers"])}
    path = directory / "verified-image.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    path.chmod(0o600)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--artifact", type=Path, required=True)
    p.add_argument("--commit", required=True)
    args = p.parse_args()
    print(json.dumps(verify(args.artifact, args.commit)))


if __name__ == "__main__":
    main()
