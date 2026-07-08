#!/usr/bin/env python3
import subprocess
import time
import os

# Test file
TEMP_AUDIO = "/tmp/test_audio.mp3"

def generate_test_audio():
    """Generate a test audio file using Google TTS"""
    from gtts import gTTS
    
    # Generate a long text to ensure we have enough audio to test with
    long_text = "This is a test of the audio playback system. We are testing to see if the audio cuts off prematurely. This sentence should be long enough to test whether the audio playback system is working correctly. Let's add even more text to make sure we have a good sample. The quick brown fox jumps over the lazy dog. The early bird catches the worm. A stitch in time saves nine. Don't count your chickens before they hatch."
    
    print(f"Generating test audio for text: {long_text}")
    tts = gTTS(text=long_text, lang='en', slow=False)
    tts.save(TEMP_AUDIO)
    print(f"Test audio saved to {TEMP_AUDIO}")

def test_mpg123():
    """Test playback using mpg123"""
    print("\nTesting mpg123...")
    start_time = time.time()
    subprocess.call(["mpg123", "-q", TEMP_AUDIO])
    end_time = time.time()
    print(f"mpg123 playback took {end_time - start_time:.2f} seconds")

def test_aplay():
    """Test playback using aplay (after converting to wav)"""
    print("\nTesting aplay...")
    wav_file = TEMP_AUDIO.replace('.mp3', '.wav')
    os.system(f"ffmpeg -y -i {TEMP_AUDIO} {wav_file} -loglevel quiet")
    
    start_time = time.time()
    subprocess.call(["aplay", wav_file])
    end_time = time.time()
    print(f"aplay playback took {end_time - start_time:.2f} seconds")
    
    if os.path.exists(wav_file):
        os.remove(wav_file)

def test_system_command():
    """Test playback using system command"""
    print("\nTesting system command...")
    start_time = time.time()
    os.system(f"mpg123 -q {TEMP_AUDIO}")
    end_time = time.time()
    print(f"System command playback took {end_time - start_time:.2f} seconds")

def test_direct_command():
    """Test playback by directly calling the command"""
    print("\nTesting direct command...")
    start_time = time.time()
    os.system(f"mpg123 -q {TEMP_AUDIO}")
    end_time = time.time()
    print(f"Direct command playback took {end_time - start_time:.2f} seconds")

def test_mplayer():
    """Test playback using mplayer if available"""
    if os.system("which mplayer > /dev/null") == 0:
        print("\nTesting mplayer...")
        start_time = time.time()
        os.system(f"mplayer -really-quiet {TEMP_AUDIO}")
        end_time = time.time()
        print(f"mplayer playback took {end_time - start_time:.2f} seconds")
    else:
        print("\nmplayer not available")

def cleanup():
    """Clean up test files"""
    if os.path.exists(TEMP_AUDIO):
        os.remove(TEMP_AUDIO)

# Run the tests
try:
    generate_test_audio()
    test_mpg123()
    test_aplay()
    test_system_command()
    test_direct_command()
    test_mplayer()
finally:
    cleanup()
