import os
import requests
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, ID3NoHeaderError

class MediaTagger:
    @staticmethod
    def inject_mp3_tags(file_path: str, title: str, artist: str, album: str, artwork_url: str | None = None) -> None:
        try:
            audio = ID3(file_path)
        except ID3NoHeaderError:
            audio = ID3()

        audio.add(TIT2(encoding=3, text=title))
        audio.add(TPE1(encoding=3, text=artist))
        audio.add(TALB(encoding=3, text=album or title))

        if artwork_url:
            try:
                img_data = requests.get(artwork_url, timeout=10).content
                audio.add(APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,
                    desc='Cover',
                    data=img_data
                ))
            except Exception as e:
                print(f"[Tagger] Artwork injection failed: {e}")

        audio.save(file_path, v2_version=3)