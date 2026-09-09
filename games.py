"""Interactive Discord games backed by the bot's existing SQLite database."""

from __future__ import annotations

import json
import random
import sqlite3
import threading
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

DATABASE_FILE = Path(__file__).resolve().parents[2] / "leon_tickets.db"

ISLAMIC_FACTS = [
    ("كم عدد أركان الإسلام؟", "5", ["3", "6", "7"]),
    ("كم عدد أركان الإيمان؟", "6", ["4", "5", "7"]),
    ("ما أول أركان الإسلام؟", "الشهادتان", ["الصلاة", "الزكاة", "الصيام"]),
    ("كم عدد الصلوات المفروضة يومياً؟", "5", ["3", "6", "7"]),
    ("ما قبلة المسلمين؟", "الكعبة", ["المسجد النبوي", "المسجد الأقصى", "عرفات"]),
    ("في أي شهر يصوم المسلمون؟", "رمضان", ["شعبان", "شوال", "محرم"]),
    ("ما اسم كتاب المسلمين؟", "القرآن الكريم", ["التوراة", "الزبور", "الإنجيل"]),
    ("من خاتم الأنبياء؟", "محمد ﷺ", ["إبراهيم", "موسى", "عيسى"]),
    ("ما أول سورة في القرآن؟", "الفاتحة", ["البقرة", "الإخلاص", "الناس"]),
    ("ما أطول سورة في القرآن؟", "البقرة", ["آل عمران", "النساء", "المائدة"]),
    ("ما أقصر سورة في القرآن؟", "الكوثر", ["العصر", "الإخلاص", "الفلق"]),
    ("كم عدد أجزاء القرآن؟", "30", ["20", "40", "60"]),
    ("ما ليلة خير من ألف شهر؟", "ليلة القدر", ["ليلة الإسراء", "ليلة الجمعة", "ليلة العيد"]),
    ("إلى أي مدينة هاجر النبي ﷺ؟", "المدينة المنورة", ["الطائف", "القدس", "دمشق"]),
    ("ما أول مسجد بُني في الإسلام؟", "مسجد قباء", ["المسجد النبوي", "المسجد الحرام", "الأقصى"]),
    ("من أول الخلفاء الراشدين؟", "أبو بكر الصديق", ["عمر بن الخطاب", "عثمان بن عفان", "علي بن أبي طالب"]),
    ("من ثاني الخلفاء الراشدين؟", "عمر بن الخطاب", ["أبو بكر الصديق", "عثمان بن عفان", "علي بن أبي طالب"]),
    ("من ثالث الخلفاء الراشدين؟", "عثمان بن عفان", ["أبو بكر الصديق", "عمر بن الخطاب", "علي بن أبي طالب"]),
    ("من رابع الخلفاء الراشدين؟", "علي بن أبي طالب", ["أبو بكر الصديق", "عمر بن الخطاب", "عثمان بن عفان"]),
    ("كم عدد أشهر السنة الهجرية؟", "12", ["10", "11", "13"]),
    ("ما أول شهر هجري؟", "محرم", ["صفر", "رمضان", "شوال"]),
    ("ما الشهر الذي يأتي بعد رمضان؟", "شوال", ["شعبان", "ذو القعدة", "محرم"]),
    ("ما الصلاة التي تؤدى عند طلوع الفجر؟", "الفجر", ["الظهر", "العصر", "العشاء"]),
    ("ما الصلاة التي تؤدى بعد غروب الشمس؟", "المغرب", ["الفجر", "الظهر", "العصر"]),
    ("ما الزكاة التي تؤدى عند نهاية رمضان؟", "زكاة الفطر", ["زكاة المال", "الصدقة", "الكفارة"]),
]

SURAH_NAMES = (
    "الفاتحة|البقرة|آل عمران|النساء|المائدة|الأنعام|الأعراف|الأنفال|التوبة|"
    "يونس|هود|يوسف|الرعد|إبراهيم|الحجر|النحل|الإسراء|الكهف|مريم|طه|"
    "الأنبياء|الحج|المؤمنون|النور|الفرقان|الشعراء|النمل|القصص|العنكبوت|"
    "الروم|لقمان|السجدة|الأحزاب|سبأ|فاطر|يس|الصافات|ص|الزمر|غافر|"
    "فصلت|الشورى|الزخرف|الدخان|الجاثية|الأحقاف|محمد|الفتح|الحجرات|ق|"
    "الذاريات|الطور|النجم|القمر|الرحمن|الواقعة|الحديد|المجادلة|الحشر|"
    "الممتحنة|الصف|الجمعة|المنافقون|التغابن|الطلاق|التحريم|الملك|القلم|"
    "الحاقة|المعارج|نوح|الجن|المزمل|المدثر|القيامة|الإنسان|المرسلات|"
    "النبأ|النازعات|عبس|التكوير|الانفطار|المطففين|الانشقاق|البروج|الطارق|"
    "الأعلى|الغاشية|الفجر|البلد|الشمس|الليل|الضحى|الشرح|التين|العلق|"
    "القدر|البينة|الزلزلة|العاديات|القارعة|التكاثر|العصر|الهمزة|الفيل|"
    "قريش|الماعون|الكوثر|الكافرون|النصر|المسد|الإخلاص|الفلق|الناس"
).split("|")

HIJRI_MONTHS = (
    "محرم|صفر|ربيع الأول|ربيع الآخر|جمادى الأولى|جمادى الآخرة|"
    "رجب|شعبان|رمضان|شوال|ذو القعدة|ذو الحجة"
).split("|")

GENERAL_FACTS = [
    ("ما عاصمة الكويت؟", "الكويت", ["الرياض", "الدوحة", "مسقط"]),
    ("كم عدد أيام الأسبوع؟", "7", ["5", "6", "8"]),
    ("أي كوكب يُعرف بالكوكب الأحمر؟", "المريخ", ["الأرض", "الزهرة", "عطارد"]),
    ("ما أكبر محيط؟", "المحيط الهادئ", ["الأطلسي", "الهندي", "المتجمد الشمالي"]),
    ("كم دقيقة في الساعة؟", "60", ["30", "45", "90"]),
    ("ما أسرع حيوان بري؟", "الفهد", ["الأسد", "الحصان", "الذئب"]),
    ("ما الغاز الذي نتنفسه؟", "الأكسجين", ["الهيدروجين", "الهيليوم", "النيتروجين"]),
    ("كم قارة في العالم؟", "7", ["5", "6", "8"]),
    ("ما أكبر كوكب؟", "المشتري", ["زحل", "الأرض", "نبتون"]),
    ("ما الحيوان الملقب بسفينة الصحراء؟", "الجمل", ["الحصان", "الفيل", "الغزال"]),
]

GUESS_CATEGORIES = {
    "حيوانات": ["أسد", "نمر", "فيل", "زرافة", "جمل", "حصان", "ذئب", "ثعلب", "دب", "قرد"],
    "طيور": ["صقر", "نسر", "حمامة", "ببغاء", "طاووس", "بطريق", "نعامة", "بومة", "غراب", "عصفور"],
    "فواكه": ["تفاح", "موز", "برتقال", "عنب", "فراولة", "بطيخ", "مانجو", "أناناس", "خوخ", "رمان"],
    "خضروات": ["طماطم", "خيار", "جزر", "بطاطا", "بصل", "فلفل", "خس", "سبانخ", "قرنبيط", "باذنجان"],
    "دول": ["الكويت", "السعودية", "قطر", "البحرين", "عمان", "مصر", "المغرب", "اليابان", "كندا", "البرازيل"],
    "مدن": ["الكويت", "الرياض", "دبي", "القاهرة", "الدوحة", "مسقط", "المنامة", "جدة", "لندن", "باريس"],
    "أدوات": ["مطرقة", "مفك", "منشار", "مفتاح", "مقص", "قلم", "مسطرة", "فرشاة", "إبرة", "مجرفة"],
    "أجهزة": ["هاتف", "حاسوب", "تلفاز", "كاميرا", "راديو", "ساعة", "طابعة", "سماعة", "ميكروفون", "جهاز لوحي"],
    "رياضات": ["كرة القدم", "كرة السلة", "التنس", "السباحة", "الملاكمة", "الجري", "الرماية", "الفروسية", "الجودو", "الطائرة"],
    "مهن": ["طبيب", "مهندس", "معلم", "طيار", "مزارع", "نجار", "طباخ", "محاسب", "مصور", "ممرض"],
    "أماكن": ["مدرسة", "مستشفى", "مطار", "مكتبة", "حديقة", "ملعب", "مطعم", "سوق", "متحف", "شاطئ"],
    "مركبات": ["سيارة", "حافلة", "قطار", "طائرة", "سفينة", "دراجة", "شاحنة", "قارب", "مروحية", "مترو"],
}


class GameDatabase:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()

    def initialize(self) -> None:
        with self.lock:
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS game_questions (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    question TEXT NOT NULL,
                    options TEXT NOT NULL,
                    correct_index INTEGER NOT NULL
                )"""
            )
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS game_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    game_type TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    total INTEGER NOT NULL,
                    played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            rows = []
            islamic_questions: list[tuple[str, str, list[str]]] = []
            number_pool = [str(number) for number in range(1, 115)]
            for index, surah in enumerate(SURAH_NAMES):
                number = str(index + 1)
                islamic_questions.append(
                    (
                        f"ما ترتيب سورة {surah} في المصحف؟",
                        number,
                        number_pool,
                    )
                )
                islamic_questions.append(
                    (
                        f"ما اسم السورة رقم {number} في المصحف؟",
                        surah,
                        SURAH_NAMES,
                    )
                )
                if index > 0:
                    islamic_questions.append(
                        (
                            f"ما السورة التي تسبق سورة {surah} مباشرة؟",
                            SURAH_NAMES[index - 1],
                            SURAH_NAMES,
                        )
                    )
                if index < len(SURAH_NAMES) - 1:
                    islamic_questions.append(
                        (
                            f"ما السورة التي تلي سورة {surah} مباشرة؟",
                            SURAH_NAMES[index + 1],
                            SURAH_NAMES,
                        )
                    )
            month_numbers = [str(number) for number in range(1, 13)]
            for index, month in enumerate(HIJRI_MONTHS):
                islamic_questions.extend(
                    [
                        (
                            f"ما ترتيب شهر {month} في السنة الهجرية؟",
                            str(index + 1),
                            month_numbers,
                        ),
                        (
                            f"ما اسم الشهر الهجري رقم {index + 1}؟",
                            month,
                            HIJRI_MONTHS,
                        ),
                    ]
                )
            for question, answer, wrong in ISLAMIC_FACTS:
                islamic_questions.append((question, answer, [answer, *wrong]))

            for identifier, (question, answer, pool) in enumerate(
                islamic_questions[:500],
                start=1,
            ):
                distractors = [option for option in pool if option != answer]
                wrong = random.Random(f"islamic:{identifier}").sample(
                    distractors,
                    k=3,
                )
                options = [answer, *wrong]
                random.Random(f"islamic-options:{identifier}").shuffle(options)
                rows.append(
                    (
                        identifier,
                        "islamic",
                        question,
                        json.dumps(options, ensure_ascii=False),
                        options.index(answer),
                    )
                )
            for index, (question, answer, wrong) in enumerate(GENERAL_FACTS):
                options = [answer, *wrong]
                random.Random(f"general:{index}").shuffle(options)
                rows.append(
                    (
                        10000 + index,
                        "general",
                        question,
                        json.dumps(options, ensure_ascii=False),
                        options.index(answer),
                    )
                )
            self.connection.executemany(
                """INSERT OR REPLACE INTO game_questions
                   (id, kind, question, options, correct_index)
                   VALUES (?, ?, ?, ?, ?)""",
                rows,
            )
            self.connection.commit()

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    def questions(self, kind: str, limit: int = 10) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                """SELECT question, options, correct_index
                   FROM game_questions WHERE kind = ?
                   ORDER BY RANDOM() LIMIT ?""",
                (kind, limit),
            ).fetchall()
        return [
            {
                "question": row["question"],
                "options": json.loads(row["options"]),
                "correct_index": row["correct_index"],
            }
            for row in rows
        ]

    def save_score(self, user_id: int, game_type: str, score: int, total: int) -> None:
        with self.lock:
            self.connection.execute(
                """INSERT INTO game_scores (user_id, game_type, score, total)
                   VALUES (?, ?, ?, ?)""",
                (user_id, game_type, score, total),
            )
            self.connection.commit()

    def islamic_count(self) -> int:
        with self.lock:
            return self.connection.execute(
                "SELECT COUNT(*) FROM game_questions WHERE kind = 'islamic'"
            ).fetchone()[0]


game_database = GameDatabase(DATABASE_FILE)


class AnswerButton(discord.ui.Button["QuizView"]):
    def __init__(self, label: str, index: int) -> None:
        self.answer_index = index
        super().__init__(label=label[:80], style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if view is not None:
            await view.answer(interaction, self.answer_index)


class QuizView(discord.ui.View):
    def __init__(
        self,
        owner_id: int,
        questions: list[dict[str, Any]],
        game_type: str,
        title: str,
    ) -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.questions = questions
        self.game_type = game_type
        self.title = title
        self.index = 0
        self.score = 0
        self.refresh_buttons()

    def refresh_buttons(self) -> None:
        self.clear_items()
        for index, option in enumerate(self.questions[self.index]["options"]):
            self.add_item(AnswerButton(str(option), index))

    def embed(self, feedback: str | None = None) -> discord.Embed:
        question = self.questions[self.index]
        embed = discord.Embed(
            title=self.title,
            description=f"**السؤال {self.index + 1}/{len(self.questions)}**\n\n{question['question']}",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"النقاط: {self.score}")
        if feedback:
            embed.add_field(name="النتيجة", value=feedback, inline=False)
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "هذه الجولة ليست خاصة بك.",
            ephemeral=True,
        )
        return False

    async def answer(self, interaction: discord.Interaction, answer_index: int) -> None:
        question = self.questions[self.index]
        correct = answer_index == question["correct_index"]
        if correct:
            self.score += 1
        self.index += 1
        if self.index >= len(self.questions):
            game_database.save_score(
                self.owner_id,
                self.game_type,
                self.score,
                len(self.questions),
            )
            embed = discord.Embed(
                title=f"انتهت {self.title}",
                description=f"نتيجتك: **{self.score}/{len(self.questions)}**",
                color=discord.Color.green(),
            )
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()
            return
        self.refresh_buttons()
        await interaction.response.edit_message(
            embed=self.embed("إجابة صحيحة ✅" if correct else "إجابة غير صحيحة ❌"),
            view=self,
        )


def guessing_questions(limit: int = 10) -> list[dict[str, Any]]:
    pool = [
        (category, item)
        for category, items in GUESS_CATEGORIES.items()
        for item in items
    ]
    selected = random.sample(pool, k=min(limit, len(pool)))
    result = []
    for category, answer in selected:
        category_items = [item for item in GUESS_CATEGORIES[category] if item != answer]
        options = [answer, *random.sample(category_items, 3)]
        random.shuffle(options)
        result.append(
            {
                "question": f"خمن الشيء. التلميح: ينتمي إلى فئة **{category}**",
                "options": options,
                "correct_index": options.index(answer),
            }
        )
    return result


def register_game_commands(bot: commands.Bot) -> None:
    @bot.tree.command(name="islamic_quiz", description="ابدأ لعبة الأسئلة الإسلامية")
    async def islamic_quiz(interaction: discord.Interaction) -> None:
        questions = game_database.questions("islamic", 10)
        view = QuizView(interaction.user.id, questions, "islamic", "الأسئلة الإسلامية")
        await interaction.response.send_message(embed=view.embed(), view=view)

    @bot.tree.command(name="guessing_game", description="ابدأ لعبة التخمين")
    async def guessing_game(interaction: discord.Interaction) -> None:
        questions = guessing_questions(10)
        view = QuizView(interaction.user.id, questions, "guessing", "لعبة التخمين")
        await interaction.response.send_message(embed=view.embed(), view=view)

    @bot.tree.command(name="random_quiz", description="ابدأ لعبة أسئلة عشوائية")
    async def random_quiz(interaction: discord.Interaction) -> None:
        questions = game_database.questions("general", 7)
        view = QuizView(interaction.user.id, questions, "random", "الأسئلة العشوائية")
        await interaction.response.send_message(embed=view.embed(), view=view)
