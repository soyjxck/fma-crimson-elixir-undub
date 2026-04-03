"""Video encoding and DSI muxing for subtitled cutscenes."""

import os
import subprocess
import tempfile

from dsi_muxer import DSI


def encode_subtitled_video(ffmpeg_bin, m2v_path, ass_path, output_path):
    """Encode video with burned ASS subtitles as PS2-compatible MPEG-2.

    Uses CBR encoding with parameters matching the original TMPGEnc output.
    FMA2 video: 512x448, 29.97fps, NTSC.
    """
    r = subprocess.run([ffmpeg_bin, '-y', '-i', m2v_path,
        '-vf', f'ass={ass_path},format=yuv420p',
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
