# Server GIF Maker

Seçilen klasördeki görselleri boyutlarına göre gruplar ve her boyut için ayrı GIF üretir.

Örnek:
- `300x250-1.jpg`
- `300x250-2.jpg`
- `300x250-3.jpg`

Çıktı:
- `300x250.gif`

## Özellikler

- Web panel üzerinden klasör seçimi
- Aynı boyuttaki görselleri otomatik gruplama
- Dosya adlarını doğal/sayısal sıralama
- Tek süre veya çoklu frame süresi desteği
- FFmpeg palettegen + paletteuse ile yüksek kalite GIF
- Çıktıları ZIP olarak indirme
- Docker ile server üzerinde çalıştırma

## Local test

Önce FFmpeg kurulu olmalı.

```bash
pip install -r requirements.txt
python app.py
```

Tarayıcıdan aç:

```text
http://localhost:8000
```

## Docker ile çalıştırma

```bash
docker build -t server-gif-maker .
docker run -p 8000:8000 server-gif-maker
```

Tarayıcıdan aç:

```text
http://SERVER_IP:8000
```

## Production notları

Nginx arkasında kullanacaksan büyük klasör upload'ları için şunları ayarla:

```nginx
client_max_body_size 2G;
proxy_read_timeout 300;
proxy_send_timeout 300;
```

GIF formatı teknik olarak 256 renk ile sınırlıdır. Bu yüzden “maksimum kalite” için FFmpeg palette yöntemi kullanılır. Daha yüksek renk kalitesi gerekiyorsa GIF yerine MP4/WebM üretimi ayrıca eklenebilir.
