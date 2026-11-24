/* Minimal i18n layer: ru/en/be dictionary + t(key) + language switch.
   Loaded before client.js/settings-extra.js so both can call window.t()
   while building UI. Switching language reloads the page — with hundreds
   of dynamic strings scattered across the app, re-deriving every render
   path live isn't worth the risk; a reload guarantees every screen comes
   up correctly translated. */
(function () {
  const DICT = {
    ru: {
      nav_aria: 'Навигация Messenger', nav_home_title: 'Платформа', nav_home: 'Главная',
      nav_chats_title: 'Чаты', nav_chats: 'Чаты', nav_groups_title: 'Группы', nav_groups: 'Группы', nav_people_title: 'Сотрудники по отделам', nav_people: 'Люди',
      nav_channels_title: 'Каналы', nav_channels: 'Каналы', nav_settings_title: 'Настройки', nav_more: 'Ещё',
      search_placeholder: 'Поиск', list_resizer_aria: 'Изменить ширину списка чатов', mobile_menu_aria: 'Меню',
      chat_select_placeholder: 'Выберите чат', chat_subtitle_default: 'Безопасная внутренняя переписка',
      back_aria: 'Назад', call_title: 'Звонок', chat_search_title: 'Поиск в чате', more_title: 'Ещё',
      scroll_down_aria: 'К последнему сообщению', context_cancel_aria: 'Отменить',
      attach_title: 'Вложение', composer_placeholder: 'Сообщение', emoji_title: 'Эмодзи', send_aria: 'Отправить',
      policy_note: 'Не передавайте информацию ограниченного распространения.',
      info_title: 'Информация', close_aria: 'Закрыть',

      settings_title: 'Настройки', settings_search_placeholder: 'Поиск в настройках',
      settings_not_found: 'Ничего не найдено',
      row_account: 'Мой аккаунт', row_notif: 'Уведомления и звуки', row_chats: 'Настройки чатов',
      row_advanced: 'Продвинутые настройки', row_media: 'Звук и камера', row_lang: 'Язык',
      row_info: 'О приложении', row_admin: 'Админ-панель', admin_row_hint: 'Звонки, статусы, лимиты',
      menu_change_avatar: 'Изменить фото профиля', menu_change_name: 'Изменение имени',
      menu_logout: 'Выйти из аккаунта', logout_title: 'Выйти из аккаунта?',
      logout_text: 'Вы вернётесь на экран входа на этом устройстве.', logout_yes: 'Выйти',
      about_toast: 'Belnipi.chat · корпоративный мессенджер',

      chat_settings_title: 'Настройки чатов', theme_label: 'Тема',
      theme_classic: 'Классика', theme_day: 'Дневная', theme_dark: 'Цветная', theme_night: 'Ночная',
      accent_label: 'Акцент', custom_color_title: 'Свой цвет', text_size_label: 'Размер текста', text_size_preview: 'Пример текста сообщения',
      wallpaper_label: 'Обои', wallpaper_off_title: 'Без обоев', wallpaper_upload_title: 'Загрузить своё фото',
      wallpaper_updated: 'Обои обновлены',

      msg_reply: 'Ответить', msg_copy: 'Копировать текст', msg_translate: 'Перевести', msg_forward: 'Переслать',
      msg_pin: 'Закрепить', msg_unpin: 'Открепить', msg_edit: 'Редактировать', msg_select: 'Выделить',
      msg_delete: 'Удалить', copied_toast: 'Скопировано', copy_failed_toast: 'Не удалось скопировать',

      notif_title: 'Уведомления и звуки', notif_general: 'Общие', notif_desktop: 'Уведомления на рабочем столе',
      notif_desktop_sub: 'Запросит разрешение браузера', notif_sound: 'Звук',
      notif_flash: 'Мигать заголовком вкладки', notif_flash_sub: 'Пока вкладка не в фокусе',
      notif_chats_cap: 'Уведомления из чатов', notif_direct: 'Личные чаты', notif_group: 'Группы',
      notif_reactions: 'Реакции на мои сообщения', notif_pinned: 'Закреплённые сообщения',
      notif_reaction_event: 'Отреагировали на ваше сообщение', notif_pinned_event: 'Закреплено новое сообщение', msg_attachment: '📎 Вложение',
      notif_calls_cap: 'Звонки', notif_calls_on_device: 'Приём звонков на этом устройстве',
      notif_calls_on_device_sub: 'Если выключено, входящие звонки автоматически отклоняются здесь',

      sc_title: 'Звук и камера', sc_speakers_cap: 'Колонки и наушники', sc_playback_device: 'Устройство воспроизведения',
      sc_default: 'По умолчанию', sc_no_output: 'Недоступно в этом браузере', sc_mic_cap: 'Микрофон',
      sc_record_device: 'Устройство записи', sc_no_mic: 'Микрофон не найден', sc_calls_cap: 'Звонки и видеочаты',
      sc_use_for_calls: 'Использовать эти устройства для звонков', sc_camera_cap: 'Камера',
      sc_no_camera: 'Камера не найдена', sc_device_n: 'Устройство {n}',

      adv_title: 'Продвинутые настройки', adv_autoload_cap: 'Автозагрузка медиа',
      adv_autoload_direct: 'В личных чатах', adv_autoload_sub: 'Изображения загружаются сразу',
      adv_autoload_group: 'В группах', adv_title_cap: 'Заголовок вкладки браузера',
      adv_show_chat_title: 'Показывать название чата', adv_show_unread: 'Показывать счётчик непрочитанных',

      lang_title: 'Язык', lang_note: 'Переключение языка применяется к интерфейсу приложения. Часть редких сообщений и уведомлений может остаться на русском.',
      lang_translate_button: 'Показать кнопку «Перевести»', lang_translate_button_sub: 'Появится в меню действий над сообщением',
      lang_search_placeholder: 'Поиск', translate_not_configured: 'Перевод сообщений ещё не подключён — нужен сервис перевода',

      app_base_title: 'Belnipi.chat', app_page_title: 'Belnipi.chat — корпоративный мессенджер',
      folder_all: 'Все', folder_unread: 'Непрочитанные', folder_direct: 'Личные', folder_group: 'Группы',
      interface_zoom: 'Масштаб интерфейса',
      status_online: 'в сети', status_away: 'отошёл', status_offline: 'не в сети', status_dnd: 'не беспокоить',
      sticker_label: 'Стикер', reply_original_message: 'Исходное сообщение',
      lang_ru: 'Русский', lang_en: 'English', lang_be: 'Беларуская',
    },
    en: {
      nav_aria: 'Messenger navigation', nav_home_title: 'Platform', nav_home: 'Home',
      nav_chats_title: 'Chats', nav_chats: 'Chats', nav_groups_title: 'Groups', nav_groups: 'Groups', nav_people_title: 'People by department', nav_people: 'People',
      nav_channels_title: 'Channels', nav_channels: 'Channels', nav_settings_title: 'Settings', nav_more: 'More',
      search_placeholder: 'Search', list_resizer_aria: 'Resize chat list', mobile_menu_aria: 'Menu',
      chat_select_placeholder: 'Select a chat', chat_subtitle_default: 'Secure internal messaging',
      back_aria: 'Back', call_title: 'Call', chat_search_title: 'Search in chat', more_title: 'More',
      scroll_down_aria: 'Jump to latest message', context_cancel_aria: 'Cancel',
      attach_title: 'Attachment', composer_placeholder: 'Message', emoji_title: 'Emoji', send_aria: 'Send',
      policy_note: 'Do not share restricted information.',
      info_title: 'Info', close_aria: 'Close',

      settings_title: 'Settings', settings_search_placeholder: 'Search settings',
      settings_not_found: 'Nothing found',
      row_account: 'My account', row_notif: 'Notifications and sounds', row_chats: 'Chat settings',
      row_advanced: 'Advanced settings', row_media: 'Sound and camera', row_lang: 'Language',
      row_info: 'About', row_admin: 'Admin panel', admin_row_hint: 'Calls, statuses, limits',
      menu_change_avatar: 'Change profile photo', menu_change_name: 'Change name',
      menu_logout: 'Log out', logout_title: 'Log out?',
      logout_text: "You'll return to the login screen on this device.", logout_yes: 'Log out',
      about_toast: 'Belnipi.chat · corporate messenger',

      chat_settings_title: 'Chat settings', theme_label: 'Theme',
      theme_classic: 'Classic', theme_day: 'Day', theme_dark: 'Colorful', theme_night: 'Night',
      accent_label: 'Accent', custom_color_title: 'Custom color', text_size_label: 'Text size', text_size_preview: 'Example message text',
      wallpaper_label: 'Wallpaper', wallpaper_off_title: 'No wallpaper', wallpaper_upload_title: 'Upload your photo',
      wallpaper_updated: 'Wallpaper updated',

      msg_reply: 'Reply', msg_copy: 'Copy text', msg_translate: 'Translate', msg_forward: 'Forward',
      msg_pin: 'Pin', msg_unpin: 'Unpin', msg_edit: 'Edit', msg_select: 'Select',
      msg_delete: 'Delete', copied_toast: 'Copied', copy_failed_toast: "Couldn't copy",

      notif_title: 'Notifications and sounds', notif_general: 'General', notif_desktop: 'Desktop notifications',
      notif_desktop_sub: "Will ask for the browser's permission", notif_sound: 'Sound',
      notif_flash: 'Flash the tab title', notif_flash_sub: "While this tab isn't focused",
      notif_chats_cap: 'Chat notifications', notif_direct: 'Direct chats', notif_group: 'Groups',
      notif_reactions: 'Reactions to my messages', notif_pinned: 'Pinned messages',
      notif_reaction_event: 'Someone reacted to your message', notif_pinned_event: 'A new message was pinned', msg_attachment: '📎 Attachment',
      notif_calls_cap: 'Calls', notif_calls_on_device: 'Receive calls on this device',
      notif_calls_on_device_sub: 'If off, incoming calls are automatically declined here',

      sc_title: 'Sound and camera', sc_speakers_cap: 'Speakers and headphones', sc_playback_device: 'Playback device',
      sc_default: 'Default', sc_no_output: "Not available in this browser", sc_mic_cap: 'Microphone',
      sc_record_device: 'Recording device', sc_no_mic: 'No microphone found', sc_calls_cap: 'Calls and video chats',
      sc_use_for_calls: 'Use these devices for calls', sc_camera_cap: 'Camera',
      sc_no_camera: 'No camera found', sc_device_n: 'Device {n}',

      adv_title: 'Advanced settings', adv_autoload_cap: 'Media autoload',
      adv_autoload_direct: 'In direct chats', adv_autoload_sub: 'Images load right away',
      adv_autoload_group: 'In groups', adv_title_cap: 'Browser tab title',
      adv_show_chat_title: 'Show chat name', adv_show_unread: 'Show unread count',

      lang_title: 'Language', lang_note: "Switching the language applies to the app's interface. A few rare messages and notifications may still show in Russian.",
      lang_translate_button: 'Show the "Translate" button', lang_translate_button_sub: "Will appear in the message action menu",
      lang_search_placeholder: 'Search', translate_not_configured: "Translation isn't connected yet — it needs a translation service",

      app_base_title: 'Belnipi.chat', app_page_title: 'Belnipi.chat — corporate messenger',
      folder_all: 'All', folder_unread: 'Unread', folder_direct: 'Direct', folder_group: 'Groups',
      interface_zoom: 'Interface scale',
      status_online: 'online', status_away: 'away', status_offline: 'offline', status_dnd: 'do not disturb',
      sticker_label: 'Sticker', reply_original_message: 'Original message',
      lang_ru: 'Русский', lang_en: 'English', lang_be: 'Беларуская',
    },
    be: {
      nav_aria: 'Навігацыя Messenger', nav_home_title: 'Платформа', nav_home: 'Галоўная',
      nav_chats_title: 'Чаты', nav_chats: 'Чаты', nav_groups_title: 'Групы', nav_groups: 'Групы', nav_people_title: 'Супрацоўнікі па аддзелах', nav_people: 'Людзі',
      nav_channels_title: 'Каналы', nav_channels: 'Каналы', nav_settings_title: 'Налады', nav_more: 'Яшчэ',
      search_placeholder: 'Пошук', list_resizer_aria: 'Змяніць шырыню спіса чатаў', mobile_menu_aria: 'Меню',
      chat_select_placeholder: 'Выберыце чат', chat_subtitle_default: 'Бяспечная ўнутраная перапіска',
      back_aria: 'Назад', call_title: 'Званок', chat_search_title: 'Пошук у чаце', more_title: 'Яшчэ',
      scroll_down_aria: 'Да апошняга паведамлення', context_cancel_aria: 'Скасаваць',
      attach_title: 'Укладанне', composer_placeholder: 'Паведамленне', emoji_title: 'Эмодзі', send_aria: 'Адправіць',
      policy_note: 'Не перадавайце інфармацыю абмежаванага распаўсюджвання.',
      info_title: 'Інфармацыя', close_aria: 'Закрыць',

      settings_title: 'Налады', settings_search_placeholder: 'Пошук у наладах',
      settings_not_found: 'Нічога не знойдзена',
      row_account: 'Мой акаунт', row_notif: 'Апавяшчэнні і гукі', row_chats: 'Налады чатаў',
      row_advanced: 'Дадатковыя налады', row_media: 'Гук і камера', row_lang: 'Мова',
      row_info: 'Пра праграму', row_admin: 'Адмін-панэль', admin_row_hint: 'Званкі, статусы, ліміты',
      menu_change_avatar: 'Змяніць фота профілю', menu_change_name: 'Змяніць імя',
      menu_logout: 'Выйсці з акаунта', logout_title: 'Выйсці з акаунта?',
      logout_text: 'Вы вернецеся на экран уваходу на гэтай прыладзе.', logout_yes: 'Выйсці',
      about_toast: 'Belnipi.chat · карпаратыўны месенджар',

      chat_settings_title: 'Налады чатаў', theme_label: 'Тэма',
      theme_classic: 'Класіка', theme_day: 'Дзённая', theme_dark: 'Каляровая', theme_night: 'Начная',
      accent_label: 'Акцэнт', custom_color_title: 'Свой колер', text_size_label: 'Памер тэксту', text_size_preview: 'Прыклад тэксту паведамлення',
      wallpaper_label: 'Шпалеры', wallpaper_off_title: 'Без шпалер', wallpaper_upload_title: 'Загрузіць сваё фота',
      wallpaper_updated: 'Шпалеры абноўлены',

      msg_reply: 'Адказаць', msg_copy: 'Капіяваць тэкст', msg_translate: 'Перакласці', msg_forward: 'Пераслаць',
      msg_pin: 'Замацаваць', msg_unpin: 'Адмацаваць', msg_edit: 'Рэдагаваць', msg_select: 'Вылучыць',
      msg_delete: 'Выдаліць', copied_toast: 'Скапіявана', copy_failed_toast: 'Не ўдалося скапіяваць',

      notif_title: 'Апавяшчэнні і гукі', notif_general: 'Агульныя', notif_desktop: 'Апавяшчэнні на працоўным стале',
      notif_desktop_sub: 'Запытае дазвол браўзера', notif_sound: 'Гук',
      notif_flash: 'Мігаць загалоўкам укладкі', notif_flash_sub: 'Пакуль укладка не ў фокусе',
      notif_chats_cap: 'Апавяшчэнні з чатаў', notif_direct: 'Асабістыя чаты', notif_group: 'Групы',
      notif_reactions: 'Рэакцыі на мае паведамленні', notif_pinned: 'Замацаваныя паведамленні',
      notif_reaction_event: 'Адрэагавалі на ваша паведамленне', notif_pinned_event: 'Замацавана новае паведамленне', msg_attachment: '📎 Укладанне',
      notif_calls_cap: 'Званкі', notif_calls_on_device: 'Прыём званкоў на гэтай прыладзе',
      notif_calls_on_device_sub: 'Калі выключана, уваходныя званкі аўтаматычна адхіляюцца тут',

      sc_title: 'Гук і камера', sc_speakers_cap: 'Калонкі і навушнікі', sc_playback_device: 'Прылада прайгравання',
      sc_default: 'Па змаўчанні', sc_no_output: 'Недаступна ў гэтым браўзеры', sc_mic_cap: 'Мікрафон',
      sc_record_device: 'Прылада запісу', sc_no_mic: 'Мікрафон не знойдзены', sc_calls_cap: 'Званкі і відэачаты',
      sc_use_for_calls: 'Выкарыстоўваць гэтыя прылады для званкоў', sc_camera_cap: 'Камера',
      sc_no_camera: 'Камера не знойдзена', sc_device_n: 'Прылада {n}',

      adv_title: 'Дадатковыя налады', adv_autoload_cap: 'Аўтазагрузка медыя',
      adv_autoload_direct: 'У асабістых чатах', adv_autoload_sub: 'Малюнкі загружаюцца адразу',
      adv_autoload_group: 'У групах', adv_title_cap: 'Загаловак укладкі браўзера',
      adv_show_chat_title: 'Паказваць назву чата', adv_show_unread: 'Паказваць лічыльнік непрачытаных',

      lang_title: 'Мова', lang_note: 'Пераключэнне мовы прымяняецца да інтэрфейсу праграмы. Частка рэдкіх паведамленняў і апавяшчэнняў можа застацца на рускай.',
      lang_translate_button: 'Паказаць кнопку «Перакласці»', lang_translate_button_sub: "З'явіцца ў меню дзеянняў над паведамленнем",
      lang_search_placeholder: 'Пошук', translate_not_configured: 'Пераклад паведамленняў яшчэ не падключаны — патрэбны сэрвіс перакладу',

      app_base_title: 'Belnipi.chat', app_page_title: 'Belnipi.chat — карпаратыўны месенджар',
      folder_all: 'Усе', folder_unread: 'Непрачытаныя', folder_direct: 'Асабістыя', folder_group: 'Групы',
      interface_zoom: 'Маштаб інтэрфейсу',
      status_online: 'у сетцы', status_away: 'адышоў', status_offline: 'не ў сетцы', status_dnd: 'не турбаваць',
      sticker_label: 'Стыкер', reply_original_message: 'Зыходнае паведамленне',
      lang_ru: 'Русский', lang_en: 'English', lang_be: 'Беларуская',
    },
  };

  const SUPPORTED = ['ru', 'en', 'be'];
  function currentLang() {
    try {
      const saved = JSON.parse(localStorage.getItem('messenger-lang-prefs') || '{}').code;
      return SUPPORTED.includes(saved) ? saved : 'ru';
    } catch (_) { return 'ru'; }
  }
  let lang = currentLang();

  function t(key, params) {
    let value = DICT[lang]?.[key] ?? DICT.ru[key] ?? key;
    if (params) Object.entries(params).forEach(([k, v]) => { value = value.replace(`{${k}}`, v); });
    return value;
  }
  function setLang(code) {
    if (!SUPPORTED.includes(code)) return;
    const prefs = { code: 'ru', translateButton: false, ...JSON.parse(localStorage.getItem('messenger-lang-prefs') || '{}') };
    prefs.code = code;
    localStorage.setItem('messenger-lang-prefs', JSON.stringify(prefs));
    window.location.reload();
  }
  function applyStaticDom(root = document) {
    if (root === document) document.documentElement.lang = lang;
    root.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
    root.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });
    root.querySelectorAll('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', t(el.dataset.i18nAria)); });
    root.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
    root.querySelectorAll('[data-i18n-ph]').forEach(el => { el.dataset.ph = t(el.dataset.i18nPh); });
  }

  window.MessengerI18n = { t, setLang, getLang: () => lang, supported: SUPPORTED, applyStaticDom };
  window.t = t;
  // Runs immediately: this file loads at the top of <body>, after the
  // static markup it targets, and — being an external script — isn't
  // blocked by the page's CSP the way an inline <script> block would be.
  applyStaticDom();
})();
