"""What the learner can practise: modes and their activities.

Each activity carries a short brief for the teacher (what to do, whom to
play). Adding a scenario means adding a row here; nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Activity:
    id: str
    mode: str                  # conversation | daily | interview | listening | ask
    title: str
    subtitle: str
    brief: str
    role: str = ""             # who the teacher plays, if anyone


CONVERSATION = Activity(
    "talk", "conversation", "Talk with your teacher", "Chat about anything; gentle corrections",
    "Have a natural conversation about whatever the learner wants to talk about. Start by "
    "asking how their day is going or what they would like to talk about. Follow their "
    "answers with genuine follow-up questions; do not jump to unrelated topics.")

ASK = Activity(
    "ask", "ask", "Ask about English", "Words, phrases, grammar questions",
    "The learner asks about English: what a word, idiom or phrasal verb means, which word to "
    "use, how to say something, or why a sentence is wrong. Explain in plain English with one "
    "short example, then invite them to use it in a sentence of their own. Start by asking "
    "what they would like to know.")

INTERVIEW = Activity(
    "interview", "interview", "Job interview", "Practise answering an interviewer",
    "Act as a professional, friendly interviewer. Ask ONE question at a time and wait for the "
    "answer. After each answer give feedback (see the interview rules), then ask the next "
    "question.", role="interviewer")

DAILY = (
    Activity("introduce", "daily", "Introducing yourself", "Meeting someone new",
             "The learner meets you for the first time at a friend's barbecue. Ask about them "
             "naturally: name, where they are from, work, interests.", "a friendly stranger"),
    Activity("my-day", "daily", "Talking about your day", "What happened today",
             "Ask the learner about their day and what they did; react and ask follow-up "
             "questions. Good for past tenses.", "a friend"),
    Activity("hobbies", "daily", "Hobbies and interests", "What you like doing",
             "Talk about the learner's hobbies and interests; share small opinions of your own "
             "and ask why they like them.", "a friend"),
    Activity("plans", "daily", "Making plans", "Arrange something with a friend",
             "You and the learner are friends arranging to meet this weekend. Negotiate day, "
             "time and place. Practise suggestions (How about...? Shall we...?).", "a friend"),
    Activity("coffee", "daily", "Ordering coffee", "At a café counter",
             "You are a barista in a busy café. Greet the learner, take their order, ask about "
             "size, milk and name, and handle payment. Speak like a real barista.", "a barista"),
    Activity("restaurant", "daily", "Ordering food", "At a restaurant",
             "You are a waiter. Seat the learner, take drink and food orders, answer questions "
             "about the menu, and bring the bill.", "a waiter"),
    Activity("shopping", "daily", "Shopping", "Asking questions in a shop",
             "You are a shop assistant in a clothing store. Help the learner find something, "
             "talk about sizes, colours and prices, and handle a return or exchange.",
             "a shop assistant"),
    Activity("directions", "daily", "Asking for directions", "Finding your way",
             "You are a local person on the street. The learner asks for directions; give "
             "realistic directions with landmarks and check they understood.", "a local"),
    Activity("appointment", "daily", "Booking an appointment", "Phoning a clinic",
             "You are a receptionist at a medical clinic on the phone. Help the learner book, "
             "move or cancel an appointment; ask for name, date of birth and a suitable time.",
             "a receptionist"),
    Activity("problem", "daily", "Explaining a problem", "Customer service",
             "You work at a customer service desk. The learner explains a problem (for example "
             "a broken item or a wrong bill). Ask clarifying questions and offer solutions.",
             "a customer service officer"),
    Activity("colleague", "daily", "Talking to a colleague", "Small talk at work",
             "You are the learner's colleague in the break room. Make small talk about work, "
             "the weekend and plans; ask about a project.", "a colleague"),
    Activity("manager", "daily", "Speaking to a manager", "Requests and updates at work",
             "You are the learner's manager. They come to you with a request or an update "
             "(time off, a problem, progress). Respond realistically and politely.", "a manager"),
    Activity("instructions", "daily", "Workplace instructions", "Understand and confirm",
             "You are a supervisor giving the learner short, realistic instructions for a task "
             "at work, two or three steps at a time. Ask them to repeat back or ask questions.",
             "a supervisor"),
    Activity("opinions", "daily", "Opinions", "Agreeing and disagreeing politely",
             "Discuss an everyday topic (for example working from home, or city versus country "
             "life). Share an opinion, ask for theirs, and help them agree or disagree politely.",
             "a friend"),
    Activity("experiences", "daily", "Past experiences", "Telling a story",
             "Ask the learner about a memorable experience (a trip, a first job, a funny "
             "moment). Encourage them to tell it as a story with details.", "a friend"),
    Activity("misunderstanding", "daily", "Handling misunderstandings", "Clarifying politely",
             "Role-play a situation where something is misunderstood (a wrong order, a mixed-up "
             "time). Help the learner ask for clarification and correct the misunderstanding.",
             "a person who misunderstood"),
)

LISTENING = (
    Activity("retell", "listening", "Listen and retell", "Hear a short story, explain it",
             "Tell a short, realistic message or story (3 to 5 sentences) suited to the "
             "learner's level, then ask them to explain it in their own words. Check what they "
             "understood, gently point out anything they missed, then offer another one."),
    Activity("repeat", "listening", "Listen and repeat", "Say sentences back",
             "Say one useful, natural sentence at a time and ask the learner to repeat it. Put "
             "the sentence in practice_target. After each attempt, give brief feedback using "
             "the practice result you are given, then give the next sentence. Increase length "
             "and difficulty when they succeed; simplify when they struggle."),
    Activity("real-world", "listening", "Real-world listening", "Announcements, messages",
             "Read a realistic piece of everyday listening: a station announcement, a voicemail, "
             "a radio weather report or a shop announcement. Then ask one or two questions about "
             "the details."),
    Activity("expressions", "listening", "Useful expressions", "Idioms and phrasal verbs",
             "Teach one common expression, idiom or phrasal verb at a time: use it in a short "
             "example, explain its meaning in plain English, then ask the learner to make their "
             "own sentence with it."),
)

ACTIVITIES = {activity.id: activity for activity in (CONVERSATION, ASK, INTERVIEW) + DAILY + LISTENING}


def get(activity_id: str) -> Activity:
    return ACTIVITIES.get(activity_id, CONVERSATION)


def page_name(activity: Activity) -> str:
    """Short name for the status bar."""
    if activity.mode == "conversation":
        return "Conversation"
    if activity.mode == "ask":
        return "Ask"
    if activity.mode == "interview":
        return "Interview"
    return activity.title
