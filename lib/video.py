"""Video encoding and DSI muxing for subtitled cutscenes."""

import os
import subprocess
import tempfile

from dsi_muxer import DSI


def _find_fontsdir():
    """Find a directory containing custom fonts for subtitle rendering."""
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fonts'),
        os.path.expanduser('~/Library/Fonts'),
        '/Library/Fonts',
        '/usr/share/fonts',
    ]
    for d in candidates:
        if os.path.isdir(d):
            return d
    return None


def encode_subtitled_video(ffmpeg_bin, m2v_path, ass_path, output_path):
    """Encode video with burned ASS subtitles as PS2-compatible MPEG-2.

    Uses CBR encoding with parameters matching the original TMPGEnc output.
    FMA2 video: 512x448, 29.97fps, NTSC.
    """
    fontsdir = _find_fontsdir()
    ass_filter = f'ass={ass_path}'
    if fontsdir:
        ass_filter += f':fontsdir={fontsdir}'

    r = subprocess.run([ffmpeg_bin, '-y', '-i', m2v_path,
        '-vf', f'{ass_filter},format=yuv420p',
        '-c:v', 'mpeg2video',
        '-b:v', '7000k', '-minrate', '7000k', '-maxrate', '7000k',
        '-bufsize', '1835008', '-qmin', '1', '-qmax', '12',
        '-s', '512x448', '-sar', '7:6', '-r', '30000/1001',
        '-g', '16', '-bf', '2', '-b_strategy', '0',
        '-mpv_flags', '+strict_gop', '-dc', '9',
        '-intra_vlc', '1', '-non_linear_quant', '1',
        '-i_qfactor', '0.4', '-b_qfactor', '4.0',
        '-color_primaries', '5', '-color_trc', '5', '-colorspace', '4',
        '-video_format', 'ntsc',
        '-an', output_path], capture_output=True, timeout=600)
    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


def build_subtitled_dsi(ffmpeg_bin, jp_dsi_path, ass_path):
    """Build a subtitled DSI from a JP DSI file and ASS subtitle file.

    Pipeline:
        1. Demux JP DSI -> video + audio
        2. Burn subtitles onto video (MPEG-2 re-encode)
        3. Remux with dsi-muxer (auto block count)

    Returns DSI bytes, or None on failure.
    """
    dsi = DSI.from_file(jp_dsi_path)
    video = dsi.extract_video()
    audio = dsi.extract_audio()

    with tempfile.TemporaryDirectory() as tmp:
        m2v_in = os.path.join(tmp, 'input.m2v')
        m2v_out = os.path.join(tmp, 'output.m2v')

        with open(m2v_in, 'wb') as f:
            f.write(video)

        if not encode_subtitled_video(ffmpeg_bin, m2v_in, ass_path, m2v_out):
            return None

        with open(m2v_out, 'rb') as f:
            new_video = f.read()

    new_dsi = DSI.mux(new_video, audio)
    return new_dsi.to_bytes()


def dump_mkv(ffmpeg_bin, m2v_path, adpcm_bytes, mkv_path):
    """Export a subtitled cutscene as MKV with JP audio.

    Args:
        ffmpeg_bin: Path to ffmpeg binary.
        m2v_path: Encoded .m2v video with burned subtitles.
        adpcm_bytes: Raw JP ADPCM audio bytes.
        mkv_path: Output MKV path.
    """
    import shutil

    with tempfile.TemporaryDirectory() as tmp:
        adpcm_path = os.path.join(tmp, 'audio.adpcm')
        txth_path = adpcm_path + '.txth'
        wav_path = os.path.join(tmp, 'audio.wav')

        with open(adpcm_path, 'wb') as f:
            f.write(adpcm_bytes)
        with open(txth_path, 'w') as f:
            f.write('codec = PSX\nchannels = 2\nsample_rate = 48000\n'
                    'interleave = 0x100\nnum_samples = data_size\n')

        vgmstream = shutil.which('vgmstream-cli')
        for p in ['/opt/homebrew/bin/vgmstream-cli', '/usr/local/bin/vgmstream-cli']:
            if not vgmstream and os.path.exists(p):
                vgmstream = p
        if vgmstream:
            subprocess.run([vgmstream, '-o', wav_path, adpcm_path],
                           capture_output=True, timeout=120)

        if os.path.exists(wav_path):
            subprocess.run([ffmpeg_bin, '-y', '-i', m2v_path, '-i', wav_path,
                '-c:v', 'libx264', '-crf', '18', '-preset', 'fast',
                '-c:a', 'aac', '-b:a', '192k',
                '-r', '30000/1001', '-shortest', mkv_path],
                capture_output=True, timeout=300)
        else:
            subprocess.run([ffmpeg_bin, '-y', '-i', m2v_path,
                '-c:v', 'libx264', '-crf', '18', '-preset', 'fast',
                '-r', '30000/1001', '-an', mkv_path],
                capture_output=True, timeout=300)
