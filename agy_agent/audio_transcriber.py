import os
import subprocess
import tempfile
import logging
from typing import Optional

logger = logging.getLogger("AudioTranscriber")

class AudioTranscriber:
    @staticmethod
    def is_audio_file(filename_or_path: str) -> bool:
        lower = filename_or_path.lower()
        return lower.endswith((
            ".ogg", ".opus", ".mp3", ".wav", ".m4a", ".aac",
            ".webm", ".flac", ".wma"
        )) or "voice-message" in lower

    @classmethod
    def transcribe_audio_sync(cls, audio_path: str) -> Optional[str]:
        """
        Converts audio (e.g. Discord voice-message.ogg) to 16kHz mono WAV using ffmpeg,
        then transcribes speech to Korean/English text using Google Speech Recognition.
        Returns transcribed text string or None if transcription is not possible.
        """
        if not os.path.isfile(audio_path):
            return None

        wav_path = None
        try:
            # 1. Convert to 16kHz mono PCM WAV via ffmpeg
            temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            wav_path = temp_wav.name
            temp_wav.close()

            cmd = [
                "ffmpeg", "-y", "-i", audio_path,
                "-ar", "16000", "-ac", "1", "-f", "wav", wav_path
            ]
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            if proc.returncode != 0:
                logger.warning(f"ffmpeg conversion failed for {audio_path}")
                return None

            # 2. Transcribe using SpeechRecognition
            import speech_recognition as sr
            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)

            # Try Korean first
            try:
                text = recognizer.recognize_google(audio_data, language="ko-KR")
                if text and text.strip():
                    logger.info(f"Audio transcription succeeded (ko-KR): {text}")
                    return text.strip()
            except sr.UnknownValueError:
                pass
            except Exception as e:
                logger.debug(f"ko-KR recognition failed: {e}")

            # Try English fallback
            try:
                text = recognizer.recognize_google(audio_data, language="en-US")
                if text and text.strip():
                    logger.info(f"Audio transcription succeeded (en-US): {text}")
                    return text.strip()
            except sr.UnknownValueError:
                pass
            except Exception as e:
                logger.debug(f"en-US recognition failed: {e}")

            return None

        except Exception as e:
            logger.error(f"Error transcribing audio {audio_path}: {e}")
            return None
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass

    @classmethod
    async def transcribe_audio(cls, audio_path: str) -> Optional[str]:
        import asyncio
        return await asyncio.to_thread(cls.transcribe_audio_sync, audio_path)
