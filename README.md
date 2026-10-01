# BanglaDub AI — Real Dubbing, No Database

এই version-এর flow:

Video upload → ElevenLabs Dubbing API → Bengali (bn) dubbing → signed audio download → FFmpeg → final MP4 → download.

## Database লাগবে?
না। Job status ছোট JSON file-এ থাকে এবং completed MP4 `outputs/`-এ থাকে। এটি MVP-এর জন্য।

## গুরুত্বপূর্ণ
- ElevenLabs API key শুধু server environment variable-এ রাখবেন। Frontend-এ নয়।
- Real AI dubbing-এর জন্য ElevenLabs account/API access এবং usage credits/limits প্রয়োজন।
- Server-এ FFmpeg থাকতে হবে; এই project-এর Dockerfile FFmpeg install করে।
- Render-এ Docker runtime দিয়ে deploy করার পর `ELEVENLABS_API_KEY` environment variable সেট করুন।
- Render-এর ephemeral filesystem-এর কারণে এটি permanent video storage নয়। Long-term video storage চাইলে পরে object storage যোগ করা উচিত।

## Render
GitHub repository-তে এই পুরো project push করুন। Render → New → Web Service → repository নির্বাচন করুন। Docker runtime/ Dockerfile ব্যবহার করুন। `render.yaml`-ও দেওয়া আছে।

Environment variable:
`ELEVENLABS_API_KEY=YOUR_KEY`

Deploy হলে URL হবে এরকম:
`https://your-service-name.onrender.com`

## Acode
`frontend/index.html` খুলে Backend URL ঘরে Render URL বসান। `Test` চাপুন। সফল হলে JSON-এ `ok: true`, `api_key_configured: true`, `ffmpeg: true` দেখতে হবে। তারপর ভিডিও নির্বাচন করে বাংলা Dubbing শুরু করুন।

## Local Docker
```bash
docker build -t bangladub-ai .
docker run --rm -p 5000:10000 -e ELEVENLABS_API_KEY=YOUR_KEY bangladub-ai
```
Then open `http://127.0.0.1:5000`.

## API
- GET `/api/health`
- POST `/api/jobs` with multipart field `video`
- GET `/api/jobs/<job_id>`
- GET `/api/jobs/<job_id>/download`

## কেন আগের JSON error ঠিক হয়েছে?
সব API error এখন JSON format-এ ফেরত আসে। Frontend response text আগে পড়ে JSON parse করে, তাই server যদি ভুল করে plain text পাঠায় তাহলেও app crash না করে পরিষ্কার error দেখাবে।
