"""ffmpeg detection and auto-building. Required for burning ASS subtitles."""

import os
import shutil
import subprocess
import platform


def find_or_build_ffmpeg():
    """Find an ffmpeg binary with libass support, or build one."""
    candidates = [
        '/tmp/ffmpeg-build/ffmpeg-7.1.1/ffmpeg',
        '/tmp/ffmpeg-custom/ffmpeg',
        shutil.which('ffmpeg'),
        '/opt/homebrew/bin/ffmpeg',
        '/usr/local/bin/ffmpeg',
    ]
    for path in candidates:
        if path and os.path.exists(path):
            r = subprocess.run([path, '-filters'], capture_output=True, text=True)
            if 'subtitles' in r.stdout or 'libass' in r.stdout:
                return path

    print("\n  ffmpeg with libass not found. Building from source...")
    print("  This may take a few minutes on first run.\n")

    build_dir = '/tmp/ffmpeg-build'
    os.makedirs(build_dir, exist_ok=True)
    system = platform.system()

    if system == 'Darwin':
        for dep in ['libass', 'libx264', 'pkgconf']:
            r = subprocess.run(['brew', 'list', dep], capture_output=True)
            if r.returncode != 0:
                print(f"  Installing {dep}...")
                subprocess.run(['brew', 'install', dep], capture_output=True)
    elif system == 'Linux':
        r = subprocess.run(['pkg-config', '--exists', 'libass'], capture_output=True)
        if r.returncode != 0:
            print("  ERROR: libass not found. Install: sudo apt install libass-dev libx264-dev pkg-config")
            return None

    ffmpeg_dir = os.path.join(build_dir, 'ffmpeg-7.1.1')
    if not os.path.exists(ffmpeg_dir):
        print("  Downloading ffmpeg source...")
        subprocess.run(['curl', '-sL', 'https://ffmpeg.org/releases/ffmpeg-7.1.1.tar.xz',
                        '-o', os.path.join(build_dir, 'ffmpeg.tar.xz')], check=True)
        subprocess.run(['tar', 'xf', os.path.join(build_dir, 'ffmpeg.tar.xz'),
                        '-C', build_dir], check=True)

    pkg_config = shutil.which('pkg-config') or shutil.which('pkgconf') or 'pkg-config'
    r = subprocess.run([pkg_config, '--cflags', 'libass'], capture_output=True, text=True)
    ass_cflags = r.stdout.strip()
    r = subprocess.run([pkg_config, '--libs', 'libass'], capture_output=True, text=True)
    ass_libs = r.stdout.strip()

    extra_cflags = ass_cflags
    extra_ldflags = ass_libs
    if system == 'Darwin':
        extra_cflags += ' -I/opt/homebrew/include'
        extra_ldflags += ' -L/opt/homebrew/lib'

    configure_args = [
        './configure', '--prefix=/tmp/ffmpeg-custom',
        '--enable-gpl', '--enable-libx264', '--enable-libass',
        f'--extra-cflags={extra_cflags}', f'--extra-ldflags={extra_ldflags}',
    ]
    if system == 'Darwin':
        configure_args.extend(['--enable-videotoolbox', '--enable-audiotoolbox'])

    print("  Configuring ffmpeg...")
    env = os.environ.copy()
    env['PKG_CONFIG'] = pkg_config
    subprocess.run(configure_args, cwd=ffmpeg_dir, capture_output=True, env=env)

    print("  Compiling ffmpeg (this takes a few minutes)...")
    subprocess.run(['make', f'-j{os.cpu_count() or 4}'], cwd=ffmpeg_dir, capture_output=True)

    ffmpeg_bin = os.path.join(ffmpeg_dir, 'ffmpeg')
    if os.path.exists(ffmpeg_bin):
        print(f"  Built: {ffmpeg_bin}")
        return ffmpeg_bin

    print("  ERROR: ffmpeg build failed")
    return None
