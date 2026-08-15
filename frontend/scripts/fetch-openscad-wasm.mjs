#!/usr/bin/env node
// Fetches the official Manifold-enabled OpenSCAD WASM build — the same
// artifact openscad.org's own openscad-playground pins in its
// libs-config.json — and unpacks openscad.js + openscad.wasm into
// public/openscad/. The openscad-wasm@0.0.4 npm package this replaces
// wasn't compiled with Manifold support, so the editor's `--backend=Manifold`
// request (see ../src/workers/openscad.worker.ts) was silently falling back
// to OpenSCAD's much slower legacy CGAL kernel. See README.md § "The
// in-browser SCAD editor".
//
// Pinned by URL + sha256 so a changed/compromised upstream file fails loud
// instead of silently unpacking something else. Not committed to git — this
// runs before dev/build (see package.json's predev/prebuild) and is
// idempotent, so a fresh clone or CI run pays one download, not every run.
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";

const ZIP_URL =
  "https://files.openscad.org/playground/OpenSCAD-2025.03.25.wasm24456-WebAssembly-web.zip";
const ZIP_SHA256 = "0968af31b9c9b3bba68d9031de1695ccae51c32231a1aab4ef27b18c86379f3b";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "..", "public", "openscad");

if (existsSync(join(outDir, "openscad.wasm")) && existsSync(join(outDir, "openscad.js"))) {
  process.exit(0);
}

console.log(`[fetch-openscad-wasm] downloading ${ZIP_URL}`);
const res = await fetch(ZIP_URL);
if (!res.ok) {
  throw new Error(`Download failed: ${res.status} ${res.statusText}`);
}
const zip = Buffer.from(await res.arrayBuffer());

const actualSha256 = createHash("sha256").update(zip).digest("hex");
if (actualSha256 !== ZIP_SHA256) {
  throw new Error(
    `Checksum mismatch for ${ZIP_URL}\n  expected ${ZIP_SHA256}\n  got      ${actualSha256}\n` +
      "Refusing to unpack an unverified build — the pinned file on files.openscad.org changed.",
  );
}

mkdirSync(outDir, { recursive: true });
for (const name of ["openscad.js", "openscad.wasm"]) {
  writeFileSync(join(outDir, name), extractZipEntry(zip, name));
  console.log(`[fetch-openscad-wasm] wrote ${name}`);
}

/**
 * Minimal ZIP reader for pulling two known entries out of the archive —
 * avoids depending on a system `unzip` being present (e.g. inside the
 * node:22-alpine Docker build stage). Reads the End Of Central Directory
 * record, walks the central directory to find each entry's local header,
 * then inflates (or copies, if stored) its data. No zip64/streaming
 * support needed — this is a small, non-streamed archive.
 */
function extractZipEntry(zip, entryName) {
  const eocdSig = 0x06054b50;
  let eocdOffset = -1;
  for (let i = zip.length - 22; i >= 0; i--) {
    if (zip.readUInt32LE(i) === eocdSig) {
      eocdOffset = i;
      break;
    }
  }
  if (eocdOffset === -1) throw new Error("Not a valid zip file (no End Of Central Directory record)");

  const cdEntryCount = zip.readUInt16LE(eocdOffset + 10);
  let cdOffset = zip.readUInt32LE(eocdOffset + 16);

  for (let i = 0; i < cdEntryCount; i++) {
    if (zip.readUInt32LE(cdOffset) !== 0x02014b50) {
      throw new Error(`Malformed central directory entry at offset ${cdOffset}`);
    }
    const method = zip.readUInt16LE(cdOffset + 10);
    const compressedSize = zip.readUInt32LE(cdOffset + 20);
    const nameLength = zip.readUInt16LE(cdOffset + 28);
    const extraLength = zip.readUInt16LE(cdOffset + 30);
    const commentLength = zip.readUInt16LE(cdOffset + 32);
    const localHeaderOffset = zip.readUInt32LE(cdOffset + 42);
    const name = zip.toString("utf8", cdOffset + 46, cdOffset + 46 + nameLength);

    if (name === entryName) {
      if (zip.readUInt32LE(localHeaderOffset) !== 0x04034b50) {
        throw new Error(`Malformed local file header for ${entryName}`);
      }
      const localNameLength = zip.readUInt16LE(localHeaderOffset + 26);
      const localExtraLength = zip.readUInt16LE(localHeaderOffset + 28);
      const dataStart = localHeaderOffset + 30 + localNameLength + localExtraLength;
      const compressed = zip.subarray(dataStart, dataStart + compressedSize);
      if (method === 0) return compressed; // stored
      if (method === 8) return inflateRawSync(compressed); // deflate
      throw new Error(`Unsupported zip compression method ${method} for ${entryName}`);
    }

    cdOffset += 46 + nameLength + extraLength + commentLength;
  }
  throw new Error(`${entryName} not found in zip`);
}
