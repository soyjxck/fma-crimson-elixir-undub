# Technical Notes

## Disc Layout

```
Root/
  SYSTEM.CNF              Boot config (BOOT2 = cdrom0:\SLUS_211.66;1)
  SLUS_211.66             1.6 MB, main executable (MIPS R5900 ELF)
  CFC.DIG                 179 MB, main data archive (Racjin compressed)
  DATA0                   200 KB, runtime scratchpad (all zeros)
  IOPRP254.IMG            IOP replacement image
  LIBSD.IRX               Sound library driver
  MUSDVD.IRX              Music/audio streaming driver
  MCMAN/MCSERV.IRX        Memory card drivers
  MODHSYN/MODMIDI.IRX     Synth/MIDI modules
  PADMAN/SIO2MAN.IRX      Controller drivers
  XA/
    XA.PAK                397 MB (USA) / 483 MB (JP), streaming audio
  DSI/
    MV00-MV11.DSI         12 cutscene files (MPEG-2 + PS2 ADPCM)
```

## Audio Pipeline (from decompilation)

```
EE (main CPU)
  -> Command Ring Buffer (64 entries @ 0x2C58C0, each 0x40 bytes)
  -> SIF RPC (service ID 0x1010)
  -> IOP (MUSDVD.IRX)
  -> SPU2
```

### XA Streaming

- `xa_0018AF70(track_index, loop_flag)` — main XA play function
- Track offset table loaded from CFC.DIG entry at dir offset 0x30 (tag 0x30000)
- Table format: 2016 entries, stride 16 bytes, 16-byte header
- Each entry: `[u32 file_offset] [u32 file_size] [8 bytes metadata]`
- Audio path: `cdrom0:\XA\XA.PAK;1`
- Streaming buffer: 0x5040 bytes (20KB ring buffer)

### Sound Effect Banks

- SCEI HD/BD format (Sony Component Sound Library)
- Embedded within CFC.DIG entries
- Magic: `IECSsreV` (reversed "SCEIVers")
- VAGInfo chunk maps samples to BD offsets with per-sample rates

### CFC.DIG Structure

- First 0x3000 bytes = directory (768 entries x 16 bytes)
- Entry format: `[u32 sector] [u32 comp_size] [u16 sections] [u16 flag] [u32 decomp_size]`
- Sector offsets are relative to CFC.DIG file start
- Flag: 0 = uncompressed, 1 = Racjin compressed

## Key Differences: USA vs JP

| Item | USA | JP |
|------|-----|-----|
| CFC.DIG ISO sector | 1362 | 1361 |
| XA.PAK size | 397 MB | 483 MB |
| Track table (CFC dir 0x30) | USA offsets | JP offsets (different layout) |
| Track table metadata | Identical | Identical |
| Logo track table (EXE) | Identical | Identical |
| SCEI sound banks | 18 differ (HD program data) | 18 differ |
| SCEI BD waveforms | 13 shared, 5 differ in size | Larger for 5 banks |
| DSI cutscene video | Re-edited for USA | Original JP |

## Pitfalls Discovered

1. **JP CFC.DIG is at sector 1361, not 1362** — reading JP CFC data with the USA sector produces garbage
2. **EXE table at 0x1780EC is NOT an XA track table** — patching it corrupts game state
3. **CFC sub-block 1 metadata must stay USA** — JP values cause half-speed audio
4. **CFC voice script patching (0x81 refs) caused half-speed** — removed from patch
5. **CFC entry relocation works but only with correct JP sector** — earlier failures were from reading JP CFC data at USA's sector offset
6. **Ghidra decompiled stride as 8 instead of 16** — verified correct stride via MIPS assembly (`sll $v0, $s1, 4`)
7. **Racjin compression is non-deterministic across runs** — patched data may compress to slightly different sizes
