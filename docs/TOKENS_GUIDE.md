# 🔑 Полное руководство по получению API токенов

Это подробное пошаговое руководство по получению всех необходимых API токенов и доступов для работы SalesWhisper Crosspost.

## 📊 Текущий статус токенов

| Платформа | Статус | Приоритет | Сложность | Время |
|-----------|--------|-----------|-----------|-------|
| Telegram | ✅ **Готово** | 🔥 Критично | Легко | 5 мин |
| VK | ✅ **Готово** | 🔥 Высокий | Легко | 10 мин |
| Security Keys | ❌ Нужно | 🔥 Критично | Легко | 1 мин |
| Instagram | ❌ Нужно | 🔥 Высокий | Средне | 30-60 мин |
| YouTube | ❌ Нужно | 🟠 Средний | Средне | 30-60 мин |
| TikTok | ❌ Нужно | 🟢 Низкий | Сложно | 1-3 дня |

---

## 1️⃣ Security Keys (КРИТИЧНО - 1 минута)

### Зачем нужно:
Эти ключи используются для шифрования токенов доступа в базе данных и генерации JWT для API.

### Как сгенерировать:

```bash
# Сгенерировать AES_KEY (32 символа для AES-256)
python3 -c "import secrets; print(secrets.token_urlsafe(32)[:32])"

# Сгенерировать TOKEN_ENCRYPTION_KEY (32 символа)
python3 -c "import secrets; print(secrets.token_urlsafe(32)[:32])"

# Сгенерировать JWT_SECRET_KEY (64 символа)
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

### Куда добавить:

Откройте файл `.env` и замените placeholder значения:

```bash
AES_KEY=<сгенерированный_ключ_1>
TOKEN_ENCRYPTION_KEY=<сгенерированный_ключ_2>
JWT_SECRET_KEY=<сгенерированный_ключ_3>
```

⚠️ **ВАЖНО:** Эти ключи нельзя менять после начала работы, иначе все зашифрованные данные станут недоступны!

---

## 2️⃣ Telegram Bot API (УЖЕ НАСТРОЕНО ✅)

### Текущие токены:

```bash
TG_BOT_TOKEN=8312487979:AAE1xoEdSf_V3vo3fHKvI45ROgPPTCN070Q
TG_PUBLISHING_BOT_TOKEN=7878387863:AAGFRj_rHkOT3-sg_CxDim9nNZooGqLWhQY
```

### Если нужно создать новый бот:

1. Откройте Telegram и найдите **@BotFather**
2. Отправьте команду `/newbot`
3. Введите имя бота: `SalesWhisper Crosspost Bot`
4. Введите username: `saleswhisper_crosspost_bot` (должен заканчиваться на `_bot`)
5. Скопируйте токен из ответа BotFather

### Настройка webhook (после деплоя):

```bash
# Установить webhook URL
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-domain.com/api/webhooks/telegram"}'

# Проверить webhook
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

---

## 3️⃣ VK API (УЖЕ НАСТРОЕНО ✅)

### Текущий токен:

```bash
VK_SERVICE_TOKEN=vk1.a.B_nX_mBDIxLzjT...
VK_GROUP_ID=123456789
```

### Если нужно создать новый токен:

1. Откройте https://vk.com/apps?act=manage
2. Нажмите **"Создать приложение"**
3. Тип: **"Standalone приложение"**
4. Название: `SalesWhisper Crosspost`
5. Перейдите в **"Настройки"** → скопируйте **App ID**
6. Получите токен через:
   ```
   https://oauth.vk.com/authorize?client_id=<APP_ID>&display=page&scope=photos,video,wall,offline&response_type=token&v=5.131&redirect_uri=https://oauth.vk.com/blank.html
   ```
7. После авторизации скопируйте `access_token` из URL

### Получить Group ID:

1. Откройте страницу вашей группы ВК
2. Group ID это число после `club` в URL: `https://vk.com/club123456789` → ID = `123456789`

---

## 4️⃣ Instagram / Meta API (30-60 минут)

### Что нужно получить:
- **META_APP_ID** - ID приложения Facebook
- **META_APP_SECRET** - секретный ключ приложения
- **META_ACCESS_TOKEN** - долгосрочный токен (60 дней)
- **Instagram Business Account ID**

### Требования:
- Аккаунт Facebook
- Instagram аккаунт переведен в **Бизнес-профиль**
- Instagram подключен к Facebook странице

---

### Шаг 1: Создать приложение Facebook

1. Откройте https://developers.facebook.com/
2. Нажмите **"Мои приложения"** (правый верхний угол)
3. Нажмите **"Создать приложение"**
4. Выберите тип: **"Бизнес"**
5. Заполните форму:
   - Отображаемое название: `SalesWhisper Crosspost`
   - Контактный email: ваш email
   - Бизнес-менеджер: можно пропустить
6. Нажмите **"Создать приложение"**

---

### Шаг 2: Добавить Instagram Graph API

1. В созданном приложении найдите **"Добавить продукт"**
2. Найдите **"Instagram Graph API"** → нажмите **"Настроить"**
3. Примите условия использования

---

### Шаг 3: Получить App ID и Secret

1. В левом меню: **"Настройки"** → **"Основное"**
2. Скопируйте:
   - **Идентификатор приложения** → это `META_APP_ID`
   - Нажмите **"Показать"** рядом с "Секрет приложения" → это `META_APP_SECRET`

Сохраните эти значения!

---

### Шаг 4: Перевести Instagram в бизнес-профиль

1. Откройте Instagram мобильное приложение
2. Перейдите в **Профиль** → **Настройки** → **Тип аккаунта и инструменты**
3. Выберите **"Перейти на профессиональный аккаунт"**
4. Выберите категорию (например, "Бренд")
5. Выберите тип: **"Бизнес"**

---

### Шаг 5: Связать Instagram с Facebook страницей

1. Откройте https://business.facebook.com/
2. Если нет бизнес-менеджера → создайте его
3. В меню: **"Настройки бизнеса"** → **"Аккаунты"** → **"Instagram аккаунты"**
4. Нажмите **"Добавить"** → **"Подключить аккаунт Instagram"**
5. Войдите в свой Instagram аккаунт
6. Убедитесь, что аккаунт отображается как подключенный

---

### Шаг 6: Создать Facebook страницу (если нет)

1. Откройте https://www.facebook.com/pages/create
2. Создайте страницу для вашего бренда
3. В настройках страницы: **"Instagram"** → подключите ваш Instagram

---

### Шаг 7: Получить краткосрочный токен

1. Откройте https://developers.facebook.com/tools/explorer/
2. В правом верхнем углу выберите ваше приложение
3. Нажмите **"Генерировать токен доступа"**
4. В списке разрешений отметьте:
   - `instagram_basic`
   - `instagram_content_publish`
   - `instagram_manage_comments`
   - `instagram_manage_insights`
   - `pages_read_engagement`
   - `pages_show_list`
   - `pages_manage_posts`
5. Нажмите **"Создать токен доступа"**
6. Скопируйте полученный токен (это **краткосрочный** токен)

---

### Шаг 8: Преобразовать в долгосрочный токен (60 дней)

Выполните команду, заменив значения:

```bash
curl -X GET "https://graph.facebook.com/v18.0/oauth/access_token?grant_type=fb_exchange_token&client_id=YOUR_APP_ID&client_secret=YOUR_APP_SECRET&fb_exchange_token=YOUR_SHORT_TOKEN"
```

Где:
- `YOUR_APP_ID` → Meta App ID из шага 3
- `YOUR_APP_SECRET` → Meta App Secret из шага 3
- `YOUR_SHORT_TOKEN` → токен из шага 7

Ответ будет содержать `access_token` - это ваш **долгосрочный токен** на 60 дней!

---

### Шаг 9: Получить Instagram Business Account ID

```bash
# Шаг 1: Получить список ваших Facebook страниц
curl -X GET "https://graph.facebook.com/v18.0/me/accounts?access_token=YOUR_LONG_LIVED_TOKEN"
```

Найдите `id` вашей страницы, затем:

```bash
# Шаг 2: Получить Instagram Business Account ID
curl -X GET "https://graph.facebook.com/v18.0/PAGE_ID?fields=instagram_business_account&access_token=YOUR_LONG_LIVED_TOKEN"
```

В ответе будет `instagram_business_account.id` - сохраните его!

---

### Шаг 10: Добавить в .env

```bash
META_APP_ID=ваш_app_id
META_APP_SECRET=ваш_app_secret
META_ACCESS_TOKEN=ваш_долгосрочный_токен
INSTAGRAM_BUSINESS_ACCOUNT_ID=ваш_ig_business_id
```

---

### Важные заметки:

⚠️ **Токен живет 60 дней** - нужно будет обновлять
⚠️ **Разработка vs Production**:
- В режиме разработки приложение может публиковать только на ваши тестовые аккаунты
- Для production нужно пройти **App Review** от Facebook (может занять 2-4 недели)

🔗 **Полезные ссылки:**
- [Instagram Graph API документация](https://developers.facebook.com/docs/instagram-api)
- [Long-Lived Tokens](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived)

---

## 5️⃣ YouTube Data API (30-60 минут)

### Что нужно получить:
- **YOUTUBE_CLIENT_ID** - OAuth Client ID
- **YOUTUBE_CLIENT_SECRET** - OAuth Client Secret
- **YOUTUBE_REFRESH_TOKEN** - Refresh token для авторизации

---

### Шаг 1: Создать проект в Google Cloud

1. Откройте https://console.cloud.google.com/
2. В верхнем меню нажмите **"Select a project"** → **"New Project"**
3. Название проекта: `SalesWhisper Crosspost`
4. Location: можно оставить "No organization"
5. Нажмите **"Create"**
6. Дождитесь создания проекта (появится уведомление)
7. Выберите созданный проект из списка

---

### Шаг 2: Включить YouTube Data API v3

1. В левом меню: **"APIs & Services"** → **"Library"**
2. В поиске введите: `YouTube Data API v3`
3. Нажмите на найденный API
4. Нажмите **"Enable"** (Включить)
5. Дождитесь активации API

---

### Шаг 3: Настроить OAuth Consent Screen

1. В левом меню: **"APIs & Services"** → **"OAuth consent screen"**
2. User Type: выберите **"External"** (для любых пользователей)
3. Нажмите **"Create"**

**Шаг 3.1: App information**
- App name: `SalesWhisper Crosspost`
- User support email: ваш email
- App logo: (можно пропустить)
- Application home page: ваш сайт или `http://localhost:8000`
- Authorized domains: ваш домен (можно пропустить для тестов)
- Developer contact information: ваш email
- Нажмите **"Save and Continue"**

**Шаг 3.2: Scopes**
- Нажмите **"Add or Remove Scopes"**
- В поиске найдите и отметьте:
  - `https://www.googleapis.com/auth/youtube.upload`
  - `https://www.googleapis.com/auth/youtube`
  - `https://www.googleapis.com/auth/youtube.force-ssl`
- Нажмите **"Update"**
- Нажмите **"Save and Continue"**

**Шаг 3.3: Test users**
- Нажмите **"Add Users"**
- Добавьте ваш Google аккаунт (email)
- Нажмите **"Add"**
- Нажмите **"Save and Continue"**

**Шаг 3.4: Summary**
- Проверьте настройки
- Нажмите **"Back to Dashboard"**

---

### Шаг 4: Создать OAuth 2.0 Credentials

1. В левом меню: **"APIs & Services"** → **"Credentials"**
2. Нажмите **"+ Create Credentials"** → **"OAuth client ID"**
3. Application type: выберите **"Web application"**
4. Name: `SalesWhisper Crosspost Web Client`
5. **Authorized redirect URIs**:
   - Нажмите **"Add URI"**
   - Добавьте: `http://localhost:8000/auth/youtube/callback`
   - Для production добавьте: `https://your-domain.com/auth/youtube/callback`
6. Нажмите **"Create"**

---

### Шаг 5: Скачать credentials

1. После создания появится popup с:
   - **Your Client ID** → скопируйте (это `YOUTUBE_CLIENT_ID`)
   - **Your Client Secret** → скопируйте (это `YOUTUBE_CLIENT_SECRET`)
2. Нажмите **"OK"**
3. Также можно скачать JSON файл с credentials (кнопка скачивания)

---

### Шаг 6: Получить Refresh Token

Создайте файл `get_youtube_token.py`:

```python
#!/usr/bin/env python3
import os
from google_auth_oauthlib.flow import InstalledAppFlow

# Замените на ваши значения
CLIENT_ID = "your_client_id"
CLIENT_SECRET = "your_client_secret"

SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube'
]

# Создаем client config
client_config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost:8000"]
    }
}

# Запускаем OAuth flow
flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
credentials = flow.run_local_server(port=8000)

print("\n=== YouTube API Credentials ===")
print(f"Access Token: {credentials.token}")
print(f"Refresh Token: {credentials.refresh_token}")
print(f"Token URI: {credentials.token_uri}")
print(f"Client ID: {credentials.client_id}")
print(f"Client Secret: {credentials.client_secret}")
```

Запустите:

```bash
# Установите зависимости
pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client

# Запустите скрипт
python3 get_youtube_token.py
```

Откроется браузер:
1. Войдите в ваш Google аккаунт
2. Разрешите доступ к YouTube
3. В терминале появится **Refresh Token** - скопируйте его!

---

### Шаг 7: Добавить в .env

```bash
YOUTUBE_CLIENT_ID=ваш_client_id.apps.googleusercontent.com
YOUTUBE_CLIENT_SECRET=ваш_client_secret
YOUTUBE_REFRESH_TOKEN=ваш_refresh_token
```

---

### Важные ограничения:

⚠️ **Квоты API:**
- По умолчанию: **10,000 units/день**
- Загрузка 1 видео = ~1,600 units
- **Итого: ~6 видео в день** на бесплатном тарифе

📈 **Как увеличить квоты:**
1. Перейдите: **"APIs & Services"** → **"YouTube Data API v3"** → **"Quotas"**
2. Нажмите **"Request quota increase"**
3. Заполните форму с обоснованием
4. Одобрение может занять 1-3 дня

🔗 **Полезные ссылки:**
- [YouTube Data API Docs](https://developers.google.com/youtube/v3)
- [Upload Videos Guide](https://developers.google.com/youtube/v3/guides/uploading_a_video)
- [Quota Calculator](https://developers.google.com/youtube/v3/determine_quota_cost)

---

## 6️⃣ TikTok Content Posting API (1-3 дня на одобрение)

### Что нужно получить:
- **TIKTOK_CLIENT_KEY**
- **TIKTOK_CLIENT_SECRET**
- **Одобрение** для Content Posting API

---

### Шаг 1: Регистрация в TikTok for Developers

1. Откройте https://developers.tiktok.com/
2. Нажмите **"Sign up"** или **"Login"**
3. Войдите через обычный TikTok аккаунт
4. Заполните профиль разработчика:
   - Company name: `SalesWhisper`
   - Website: ваш сайт
   - Email: ваш email
5. Примите Terms of Service

---

### Шаг 2: Создать приложение

1. После входа нажмите **"My apps"**
2. Нажмите **"Create new app"**
3. Заполните форму:
   - **App name**: `SalesWhisper Crosspost`
   - **App type**: Server-to-Server (для автоматической публикации)
   - **Description**: Подробно опишите:
     ```
     SalesWhisper Crosspost is an automated content distribution system that helps
     fashion brand SalesWhisper publish product videos across multiple social media
     platforms. The app will post videos about new clothing collections,
     product launches, and fashion tips from Telegram to TikTok automatically.

     Use case: Publishing 3-5 product videos per day showcasing SalesWhisper brand
     fashion items to TikTok audience.
     ```
   - **Category**: Social / Lifestyle
   - **App website**: ваш домен
   - **Privacy Policy URL**: (обязательно!)
   - **Terms of Service URL**: (обязательно!)
4. Нажмите **"Submit"**

⚠️ **ВАЖНО:** Нужны реальные Privacy Policy и Terms of Service на вашем сайте!

---

### Шаг 3: Запросить доступ к Content Posting API

1. В созданном приложении → **"Add products"**
2. Найдите **"Content Posting API"**
3. Нажмите **"Apply"**
4. Заполните форму заявки:
   - **Purpose**: Publishing branded fashion content
   - **Expected monthly uploads**: 90-150 videos/month
   - **Sample content**: загрузите пример видео
   - **Business verification**: загрузите документы компании
5. Нажмите **"Submit for review"**

---

### Шаг 4: Ожидание одобрения

⏱️ **Время ожидания:** 1-7 дней

Статусы:
- **In Review** - заявка рассматривается
- **Approved** - одобрено ✅
- **Rejected** - отклонено (можно подать заново)

Пока ждете одобрения, можно использовать **Sandbox режим** для тестов.

---

### Шаг 5: Получить Client Key и Secret

1. В приложении перейдите в **"Credentials"**
2. Скопируйте:
   - **Client Key** → это `TIKTOK_CLIENT_KEY`
   - **Client Secret** (нажмите "Show") → это `TIKTOK_CLIENT_SECRET`

---

### Шаг 6: Настроить OAuth Redirect

1. В **"Redirect URIs"** добавьте:
   ```
   http://localhost:8000/auth/tiktok/callback
   https://your-domain.com/auth/tiktok/callback
   ```
2. Нажмите **"Save"**

---

### Шаг 7: Получить Access Token (после одобрения)

Нужно пройти OAuth flow для получения токена:

```python
# Пример кода для получения токена
import requests

CLIENT_KEY = "your_client_key"
REDIRECT_URI = "http://localhost:8000/auth/tiktok/callback"

# Шаг 1: Получить authorization URL
auth_url = (
    f"https://www.tiktok.com/v2/auth/authorize/"
    f"?client_key={CLIENT_KEY}"
    f"&scope=user.info.basic,video.publish"
    f"&response_type=code"
    f"&redirect_uri={REDIRECT_URI}"
)

print(f"Откройте в браузере:\n{auth_url}")

# Пользователь авторизуется, получает 'code' в redirect URL

# Шаг 2: Обменять code на token
code = input("Введите код из URL: ")

response = requests.post(
    "https://open-api.tiktok.com/oauth/access_token/",
    data={
        "client_key": CLIENT_KEY,
        "client_secret": "your_client_secret",
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI
    }
)

data = response.json()
print(f"Access Token: {data['data']['access_token']}")
print(f"Refresh Token: {data['data']['refresh_token']}")
```

---

### Шаг 8: Добавить в .env

```bash
TIKTOK_CLIENT_KEY=ваш_client_key
TIKTOK_CLIENT_SECRET=ваш_client_secret
TIKTOK_ACCESS_TOKEN=ваш_access_token
TIKTOK_REFRESH_TOKEN=ваш_refresh_token
```

---

### Режимы работы:

1. **Sandbox (Testing)**:
   - Доступен сразу после создания приложения
   - Видео публикуются как **Drafts** (черновики)
   - Не видны публично
   - Идеально для тестирования

2. **Production**:
   - Требует одобрения Content Posting API
   - Видео публикуются публично
   - Полный функционал

---

### Важные ограничения:

⚠️ **Rate Limits:**
- **1,000 requests/day**
- **20 requests/minute**
- **1 video upload = 1 request**

⚠️ **Требования к видео:**
- Формат: MP4, MOV, MPEG, FLV, AVI, 3GPP, WEBM
- Разрешение: 540x960 - 1080x1920 (вертикальное)
- Длительность: 3 - 180 секунд
- Размер: до 500 MB
- Соотношение сторон: 9:16 (рекомендуется)

🔗 **Полезные ссылки:**
- [TikTok for Developers](https://developers.tiktok.com/)
- [Content Posting API Docs](https://developers.tiktok.com/doc/content-posting-api-get-started/)
- [Video Upload Guide](https://developers.tiktok.com/doc/content-posting-api-video-upload/)

---

## 📝 Итоговый чеклист

После получения всех токенов проверьте `.env` файл:

```bash
# ✅ Security (критично!)
AES_KEY=<32_символа>
TOKEN_ENCRYPTION_KEY=<32_символа>
JWT_SECRET_KEY=<64_символа>

# ✅ Telegram (готово)
TG_BOT_TOKEN=8312487979:AAE1xoEdSf_V3vo3fHKvI45ROgPPTCN070Q
TG_PUBLISHING_BOT_TOKEN=7878387863:AAGFRj_rHkOT3-sg_CxDim9nNZooGqLWhQY
TG_ADMIN_CHANNEL_ID=-1001234567890

# ✅ VK (готово)
VK_SERVICE_TOKEN=vk1.a.B_nX_mBDIxLzjT...
VK_GROUP_ID=123456789

# 🔄 Instagram (нужно заполнить)
META_APP_ID=<your_app_id>
META_APP_SECRET=<your_app_secret>
META_ACCESS_TOKEN=<your_long_lived_token>
INSTAGRAM_BUSINESS_ACCOUNT_ID=<your_ig_business_id>

# 🔄 YouTube (нужно заполнить)
YOUTUBE_CLIENT_ID=<your_client_id>.apps.googleusercontent.com
YOUTUBE_CLIENT_SECRET=<your_client_secret>
YOUTUBE_REFRESH_TOKEN=<your_refresh_token>

# 🔄 TikTok (нужно заполнить)
TIKTOK_CLIENT_KEY=<your_client_key>
TIKTOK_CLIENT_SECRET=<your_client_secret>
TIKTOK_ACCESS_TOKEN=<your_access_token>
TIKTOK_REFRESH_TOKEN=<your_refresh_token>

# 🔄 LLM для генерации контента (опционально)
OPENAI_API_KEY=<your_openai_key>
```

---

## 🧪 Проверка токенов

После настройки всех токенов проверьте их работоспособность:

```bash
# Запустите тестовый скрипт
docker-compose exec api python -m app.scripts.test_tokens

# Или вручную через API
curl http://localhost:8000/api/accounts/test
```

---

## 🆘 Troubleshooting

### Проблема: Instagram токен истек
**Решение:** Повторите шаги 7-8 для получения нового долгосрочного токена

### Проблема: YouTube квоты исчерпаны
**Решение:** Подождите до следующего дня или запросите увеличение квот

### Проблема: TikTok заявка отклонена
**Решение:**
1. Улучшите описание приложения
2. Добавьте более детальные use cases
3. Загрузите качественные примеры контента
4. Убедитесь, что Privacy Policy соответствует требованиям
5. Подайте заявку повторно

### Проблема: VK токен не работает
**Решение:** Проверьте scope разрешений, должны быть: `photos,video,wall,offline`

---

## 📞 Поддержка

Если возникли проблемы с получением токенов:

1. **Проверьте официальную документацию** платформы
2. **Посмотрите примеры** в коде проекта (`app/adapters/`)
3. **Создайте issue** на GitHub с описанием проблемы

---

*Документация обновлена: 2025-01-XX*
