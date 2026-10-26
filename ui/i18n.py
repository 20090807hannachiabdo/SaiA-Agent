"""
ui/i18n.py - Centralized Internationalization Management
English is the primary source language, with full localization for Arabic and French.
"""

from typing import Dict, Any

DEFAULT_LANG = "en"

# Display name in language picker -> language code
LANGUAGES: Dict[str, str] = {
    "English": "en",
    "العربية": "ar",
    "Français": "fr",
}

STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        "app_title": "SaiA Agent",
        "assistant": "ASSISTANT",
        "agent_terminal": "AGENT TERMINAL",
        "send": "Send",
        "thinking": "Thinking",
        "you": "You",
        "done": "✓ Done.",
        "error_exec": "⚠ Execution Error: {error}",
        "settings_title": "SaiA — Settings",
        "settings": "SETTINGS",
        "back": "← Back",
        "about": "About",
        "general": "GENERAL",
        "theme_to_light": "☀ Light Mode",
        "theme_to_dark": "☾ Dark Mode",
        "language": "Language",
        "profile": "PROFILE",
        "user_nickname": "Nickname",
        "save": "Save",
        "saved": "Saved ✓",
        "subscription": "Subscription",
        "account": "ACCOUNT",
        "login": "Log In",
        "sign_up": "Create Account",
        "ai_model": "AI MODEL",
        "model": "Model",
        "specialist": "Specialist",
        "no_models": "No models available",
        "status_preparing": "Preparing the task",
        "status_fixing": "Fixing an issue and retrying",
        "status_finishing": "Wrapping up",
        "status_media": "Generating the media file",
        
        # Chat History Drawer
        "chat_history": "Chat History",
        "new_chat": "+ New Chat",
        "no_chats": "No past conversations found.",
        "rename_dialog_title": "Rename Conversation",
        "rename_dialog_label": "New Title:",
        
        # Visual Flow Studio (n8n-style)
        "flow_studio_title": "SaiA Flow Studio",
        "flow_scheduler_active": "● Windows Scheduler: Active",
        "add_new_flow": "+ Add New Flow",
        "refresh": "🔄 Refresh",
        "no_flows_desc": "No active automation flows.\nClick [+ Add New Flow] to schedule an autonomous background task.",
        "node_trigger": "⏰ Trigger",
        "node_every_minutes": "Every {mins} min",
        "node_persona": "🤖 AI Persona",
        "node_action": "⚙ Action Task",
        "flow_run_now": "▶ Run Now",
        "flow_active": "Active ✓",
        "flow_inactive": "Inactive ✕",
        "dialog_new_flow": "Create Automation Flow",
        "dialog_flow_name": "Flow Name:",
        "dialog_interval_min": "Interval (Minutes):",
        "dialog_specialist": "Specialist Persona:",
        "dialog_prompt": "Autonomous Prompt:",
        "dialog_save_activate": "Save & Activate in Background",
        "flow_activating": "Activating background task",
        "flow_activated": "✓ Task activated",
        "flow_activation_failed": "✕ Activation failed, see automation.log",
        
        # Fallbacks
        "new_conversation": "New Chat",
    },
    
    "ar": {
        "app_title": "وكيل SaiA الذكي",
        "assistant": "المساعد",
        "agent_terminal": "AGENT TERMINAL",
        "send": "إرسال",
        "thinking": "يفكر",
        "you": "أنت",
        "done": "✓ تم.",
        "error_exec": "⚠ خطأ أثناء التنفيذ: {error}",
        "settings_title": "SaiA — الإعدادات",
        "settings": "الإعدادات",
        "back": "← رجوع",
        "about": "حول",
        "general": "عام",
        "theme_to_light": "☀ الوضع المضيء",
        "theme_to_dark": "☾ الوضع الداكن",
        "language": "اللغة",
        "profile": "الملف الشخصي",
        "user_nickname": "التلقيب",
        "save": "حفظ",
        "saved": "تم الحفظ ✓",
        "subscription": "الاشتراك",
        "account": "الحساب",
        "login": "تسجيل الدخول",
        "sign_up": "إنشاء حساب",
        "ai_model": "نموذج الذكاء الاصطناعي",
        "model": "النموذج",
        "specialist": "التخصص",
        "no_models": "لا توجد نماذج متاحة",
        "status_preparing": "جاري تحضير المهمة",
        "status_fixing": "جاري إصلاح مشكلة وإعادة المحاولة",
        "status_finishing": "جاري إنهاء المهمة",
        "status_media": "جاري إنشاء الملف",
        
        # Chat History Drawer
        "chat_history": "سجل المحادثات",
        "new_chat": "+ محادثة جديدة",
        "no_chats": "لا توجد محادثات سابقة.",
        "rename_dialog_title": "تعديل اسم المحادثة",
        "rename_dialog_label": "الاسم الجديد:",
        
        # Visual Flow Studio (n8n-style)
        "flow_studio_title": "أستوديو أتمتة التدفقات (Flow Studio)",
        "flow_scheduler_active": "● مجدول ويندوز: نشط",
        "add_new_flow": "+ إضافة تدفق جديد",
        "refresh": "🔄 تحديث",
        "no_flows_desc": "لا توجد تدفقات أتمتة حالياً.\nاضغط على [+ إضافة تدفق جديد] لإنشاء مهمة تعمل بالخلفية حتى بعد إغلاق التطبيق.",
        "node_trigger": "⏰ المحفّز",
        "node_every_minutes": "كل {mins} دقيقة",
        "node_persona": "🤖 التخصص المنفذ",
        "node_action": "⚙ الإجراء التلقائي",
        "flow_run_now": "▶ تشغيل الآن",
        "flow_active": "نشط ✓",
        "flow_inactive": "معطل ✕",
        "dialog_new_flow": "إنشاء تدفق أتمتة جديد",
        "dialog_flow_name": "عنوان التدفق:",
        "dialog_interval_min": "التكرار (بالدقائق):",
        "dialog_specialist": "التخصص المنفذ:",
        "dialog_prompt": "أمر الذكاء الاصطناعي (Prompt):",
        "dialog_save_activate": "حفظ وتفعيل في الخلفية",
        "flow_activating": "جاري تفعيل المهمة في الخلفية",
        "flow_activated": "✓ تم تفعيل المهمة",
        "flow_activation_failed": "✕ فشل التفعيل، راجع automation.log",
        
        # Fallbacks
        "new_conversation": "محادثة جديدة",
    },
    
    "fr": {
        "app_title": "Agent SaiA",
        "assistant": "ASSISTANT",
        "agent_terminal": "TERMINAL AGENT",
        "send": "Envoyer",
        "thinking": "Réflexion",
        "you": "Vous",
        "done": "✓ Terminé.",
        "error_exec": "⚠ Erreur d'exécution : {error}",
        "settings_title": "SaiA — Paramètres",
        "settings": "PARAMÈTRES",
        "back": "← Retour",
        "about": "À propos",
        "general": "GÉNÉRAL",
        "theme_to_light": "☀ Mode clair",
        "theme_to_dark": "☾ Mode sombre",
        "language": "Langue",
        "profile": "PROFIL",
        "user_nickname": "Pseudo",
        "save": "Enregistrer",
        "saved": "Enregistré ✓",
        "subscription": "Abonnement",
        "account": "COMPTE",
        "login": "Connexion",
        "sign_up": "Créer un compte",
        "ai_model": "MODÈLE IA",
        "model": "Modèle",
        "specialist": "Spécialité",
        "no_models": "Aucun modèle disponible",
        "status_preparing": "Préparation de la tâche",
        "status_fixing": "Correction d'erreur en cours",
        "status_finishing": "Finalisation",
        "status_media": "Génération du média",
        
        # Chat History Drawer
        "chat_history": "Historique des discussions",
        "new_chat": "+ Nouvelle discussion",
        "no_chats": "Aucune discussion antérieure trouvée.",
        "rename_dialog_title": "Renommer la discussion",
        "rename_dialog_label": "Nouveau titre :",
        
        # Visual Flow Studio (n8n-style)
        "flow_studio_title": "Studio de Flux SaiA",
        "flow_scheduler_active": "● Planificateur Windows : Actif",
        "add_new_flow": "+ Nouveau flux",
        "refresh": "🔄 Actualiser",
        "no_flows_desc": "Aucun flux d'automatisation actif.\nCliquez sur [+ Nouveau flux] pour planifier une tâche en arrière-plan.",
        "node_trigger": "⏰ Déclencheur",
        "node_every_minutes": "Toutes les {mins} min",
        "node_persona": "🤖 Rôle IA",
        "node_action": "⚙ Action autonome",
        "flow_run_now": "▶ Exécuter",
        "flow_active": "Actif ✓",
        "flow_inactive": "Inactif ✕",
        "dialog_new_flow": "Créer un flux d'automatisation",
        "dialog_flow_name": "Nom du flux :",
        "dialog_interval_min": "Intervalle (minutes) :",
        "dialog_specialist": "Rôle spécialisé :",
        "dialog_prompt": "Instruction IA :",
        "dialog_save_activate": "Enregistrer et activer",
        "flow_activating": "Activation de la tâche en arrière-plan",
        "flow_activated": "✓ Tâche activée",
        "flow_activation_failed": "✕ Échec de l'activation, voir automation.log",
        
        # Fallbacks
        "new_conversation": "Nouvelle discussion",
    },
}

_current_language = DEFAULT_LANG


def set_language(code: str) -> None:
    """Sets the active system language code."""
    global _current_language
    _current_language = code if code in STRINGS else DEFAULT_LANG


def get_language() -> str:
    """Returns the current active language code."""
    return _current_language


def t(key: str, **kwargs: Any) -> str:
    """Translates a key into current language with English fallback."""
    text = STRINGS.get(_current_language, {}).get(key)
    if text is None:
        text = STRINGS[DEFAULT_LANG].get(key, key)
    return text.format(**kwargs) if kwargs else text
