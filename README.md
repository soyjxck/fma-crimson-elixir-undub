# Fullmetal Alchemist 2: Curse of the Crimson Elixir — Undub Patch

Replaces English audio with Japanese audio in the USA PS2 release.

If this helped you, consider [buying me a coffee](https://ko-fi.com/soyjack)

## What's Changed

| Content | Status |
|---------|--------|
| Cutscene dialogue (12 DSI FMVs) | Full JP video + audio (no truncation) |
| In-game voice dialogue (XA streaming) | Japanese (2016 track offsets patched) |
| Square Enix logo voice | Japanese (cycles by day of month) |
| Combat voice barks | Japanese (18 SCEI banks replaced) |
| Music / BGM | Unchanged (shared between versions) |
| Menu / UI sound effects | Unchanged |

## How to Patch

### Option 1: xdelta (recommended)

Pre-built patch. No build tools needed.

**Requirements**: USA ISO + [DeltaPatcher](https://github.com/marco-calautti/DeltaPatcher/releases)

1. Download `FMA2_Undub.xdelta` from [Releases](https://github.com/soyjxck/fma2-crimson-elixir-undub/releases/latest)
2. Open DeltaPatcher
3. **Original file**: `Fullmetal Alchemist 2 - Curse of the Crimson Elixir (USA).iso`
4. **Patch file**: `FMA2_Undub.xdelta`
5. Click **Apply patch**

```bash
# Or via command line:
xdelta3 -d -s "usa.iso" FMA2_Undub.xdelta "FMA2_Undub.iso"
```

### Option 2: Build from ISOs

Build from both USA and JP ISOs.

**Requirements**: Python 3.9+, both ISOs

```bash
git clone https://github.com/soyjxck/fma2-crimson-elixir-undub.git
cd fma2-crimson-elixir-undub
python3 patch.py "path/to/usa.iso" "path/to/jp.iso" "FMA2_Undub.iso"
```

**Optional flags:**
- `--generate-xdelta` — Also create an xdelta patch file after building
- `--skip-verify` — Skip ISO hash verification

## Source ISOs

| Version | File | MD5 |
|---------|------|-----|
| USA | `Fullmetal Alchemist 2 - Curse of the Crimson Elixir (USA).iso` | `2e79a69434561557dd0eaa9061d62eed` |
| JP | `Hagane no Renkinjutsushi 2 - Akaki Elixir no Akuma (Japan).iso` | `6804b82a9eb8d6a1e2d85a25683ec89d` |

## How It Works

### Voice Dialogue (XA.PAK)

FMA2 streams all in-game voice dialogue from `\XA\XA.PAK` — a raw PS2 ADPCM file containing 2016 tracks. The track offset table lives in CFC.DIG entry 2 (at directory offset 0x30), mapping each track index to a byte position in XA.PAK. USA and JP have **different track layouts** but the **same track indices** in game scripts.

The patch replaces the full 483MB JP XA.PAK (USA is only 397MB) and surgically patches the CFC.DIG track offset table: JP byte positions replace USA positions while keeping all USA playback metadata (sample rates, volume, channel info) intact. The table is relocated to free space since the patched version compresses slightly larger than the original.

### Cutscenes (DSI Files)

12 DSI cutscene files (MV00-MV11) are replaced with full JP versions — no truncation. The game uses Racjin's proprietary DSI container format with interleaved MPEG-2 video and PS2 ADPCM audio. Since the JP videos differ from USA (re-edited with Japanese text), full file replacement is required.

The ISO is compacted so the larger JP files fit without wasted space:
```
CFC.DIG (179MB) -> Track Table (41KB) -> DSI cutscenes (1.7GB) -> DATA0 -> XA.PAK (483MB)
```

### Combat Barks (SCEI Sound Banks)

Combat voice barks use Sony's SCEI HD/BD sound bank format embedded in CFC.DIG. While 18 banks differ between versions, the base audio waveforms (BD body) are largely shared — the differences are in the HD program/instrument definitions. Combat barks remain in English in the current build.

### What We Learned (Decompilation)

The patch was developed with the help of a **full Ghidra decompilation** of the PS2 executable (MIPS R5900, Metrowerks compiler) using the [ghidra-emotionengine-reloaded](https://github.com/chaoticgd/ghidra-emotionengine-reloaded) plugin. Key findings:

- The game uses filesystem paths (`cdrom0:\XA\XA.PAK;1`), not hardcoded sector addresses — files can be freely relocated in the ISO
- XA streaming function `xa_0018AF70` reads track offsets from a runtime-loaded table (CFC.DIG entry 2, loaded as tag 0x30000)
- The track table uses **stride 16** per entry (Ghidra incorrectly decompiled this as stride 8 — verified via MIPS assembly: `sll $v0, $s1, 4`)
- CFC.DIG's first 0x3000 bytes serve as the file directory, loaded into RAM at boot
- The USA and JP ISOs have CFC.DIG at **different sectors** (1362 vs 1361) — a critical detail for correct JP data extraction
- The EXE table at offset 0x1780EC is **not** an XA track table — patching it corrupts game state
- Replacing CFC sub-block 1 metadata with JP values causes half-speed audio playback

## Known Limitations

- Some combat bark samples may have slightly different timing due to different JP sound bank programs
- 5 tracks beyond the USA XA.PAK boundary may have no audio (JP tracks at >397MB that had no USA equivalent)

## Credits

- **soyjxck** — Reverse engineering, patch development
- **Claude** (Anthropic) — Full executable decompilation, audio pipeline analysis, patch development

## Related Projects

- [FMA1 Undub](https://github.com/soyjxck/fma-broken-angel-undub) — Undub patch for FMA: Broken Angel
- [ghidra-emotionengine-reloaded](https://github.com/chaoticgd/ghidra-emotionengine-reloaded) — PS2 EE processor plugin for Ghidra

## License

MIT
