"""
ai_helper.py - LLM-powered job intelligence using Groq's free-tier API
(OpenAI-compatible, hosted Llama 3.1 models).

Responsibilities:
- score_relevance(jd_text, profile): rate how well a job matches the
  candidate's resume/profile, 0-100, with a short reasoning string.
- summarize_jd(jd_text): condense a long job description into a few
  bullet points for quick human scanning in the decision email.

If GROQ_API_KEY is not set, or the API call fails for any reason, every
function degrades gracefully (returns a neutral score / the raw text)
so the bot keeps working exactly as before - AI is a strict enhancement,
never a hard dependency for the pipeline to function.
"""

import os
import json
import re
import functools


def _extract_json_object(raw):
    """Best-effort extraction of a JSON object from a possibly noisy/truncated
    LLM response (e.g. wrapped in prose or code fences)."""
    raw = raw.strip().strip("`")
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No valid JSON object found in response: {raw[:200]!r}")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        _client = Groq(api_key=GROQ_API_KEY)
        return _client
    except Exception as e:
        print(f"[AI] Could not initialize Groq client: {e}")
        return None


@functools.lru_cache(maxsize=1)
def load_profile():
    profile_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile.json")
    if not os.path.exists(profile_path):
        return {}
    try:
        with open(profile_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _profile_summary_text(profile):
    if not profile:
        return "No structured profile available."
    skills = ", ".join(profile.get("skills", []))
    exp_lines = []
    for exp in profile.get("experience", []):
        exp_lines.append(f"{exp.get('role')} at {exp.get('company')}: " + " ".join(exp.get("highlights", [])))
    return (
        f"Summary: {profile.get('summary', '')}\n"
        f"Skills: {skills}\n"
        f"Experience: {' | '.join(exp_lines)}"
    )


def score_relevance(jd_text, profile=None):
    """Returns dict: {"score": int 0-100, "reasoning": str}."""
    profile = profile if profile is not None else load_profile()
    client = _get_client()
    if client is None or not jd_text or jd_text.strip() in ("Not available", ""):
        return {"score": 50, "reasoning": "AI scoring unavailable - showing job by default."}

    prompt = (
        "You are an assistant helping a job seeker triage job postings. "
        "Given the candidate profile and a job description, rate how well "
        "the candidate matches this job from 0-100, and give a one-sentence reason. "
        "Respond ONLY with compact JSON: {\"score\": <int>, \"reasoning\": \"<text>\"}.\n\n"
        f"CANDIDATE PROFILE:\n{_profile_summary_text(profile)}\n\n"
        f"JOB DESCRIPTION:\n{jd_text[:3000]}"
    )
    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=800,
            reasoning_effort="low",
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        if not raw:
            raise ValueError("empty response from model")
        data = _extract_json_object(raw)
        score = int(data.get("score", 50))
        reasoning = str(data.get("reasoning", ""))
        return {"score": max(0, min(100, score)), "reasoning": reasoning}
    except Exception as e:
        print(f"[AI] score_relevance failed, defaulting to neutral score: {e}")
        return {"score": 50, "reasoning": "AI scoring failed - showing job by default."}


def answer_question(question, profile=None, job_title="", company=""):
    """Answers an open-ended application/questionnaire question in first
    person, grounded in the candidate's real resume profile. Returns a
    plain-text answer string, or None if AI is unavailable."""
    profile = profile if profile is not None else load_profile()
    client = _get_client()
    if client is None or not question:
        return None

    context = f" for the role of {job_title} at {company}" if job_title else ""
    prompt = (
        f"You are Vidur Sharma, answering a job application questionnaire{context}. "
        "Write like a real person typing a quick, honest answer in a form field - "
        "NOT like a polished essay or a corporate brochure. Rules:\n"
        "- Answer ONLY what was actually asked. Do not pad with extra achievements, "
        "extra skills, or unrelated context just to sound impressive.\n"
        "- 2-3 short sentences max. No bullet points, no headers.\n"
        "- Use plain, conversational, human language - contractions are fine "
        "(e.g. \"I've\", \"I'm\"), avoid stiff phrases like \"I possess\", \"leverage\", "
        "\"passionate about delivering\", \"proven track record\" - recruiters can spot "
        "AI-generated corporate-speak instantly and it hurts the application.\n"
        "- Base everything strictly on the real background below. Never invent employers, "
        "tools, numbers, or specific incidents/anecdotes not listed - do NOT make up a fake "
        "story, teammate's name, bug, or specific event that isn't in the background.\n"
        "- For behavioral/anecdotal questions (conflict, failure, project going wrong, "
        "learning something new, teamwork) where the background has no matching real story, "
        "answer honestly with your genuine approach/principles instead of a fabricated "
        "specific incident. Speak like a calm, responsible, ownership-driven engineer who "
        "leads by example - someone who stays composed under pressure, communicates early, "
        "takes accountability without blaming others, and helps the team stay aligned - "
        "without claiming a specific event happened if it didn't.\n"
        "- Keep the tone mature, steady, and collaborative (natural team-lead instincts even "
        "as an early-career engineer), never arrogant or overconfident.\n\n"
        f"YOUR BACKGROUND:\n{_profile_summary_text(profile)}\n\n"
        f"QUESTION: {question}"
    )
    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=500,
            reasoning_effort="low",
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[AI] answer_question failed: {e}")
        return None


def tailor_skills_for_jd(jd_text, profile=None, fallback_skills=""):
    """Reorders/rephrases the candidate's REAL skills to emphasize whichever
    ones the JD actually asks for, using the JD's own terminology where an
    equivalent genuine skill exists. Never invents a skill the candidate
    doesn't actually have - this is keyword/ATS alignment, not fabrication.
    Returns a comma-separated string, or fallback_skills if AI is unavailable."""
    profile = profile if profile is not None else load_profile()
    client = _get_client()
    known_skills = fallback_skills or ", ".join(profile.get("skills", []))
    if client is None or not jd_text or jd_text.strip() in ("Not available", "") or not known_skills:
        return fallback_skills

    prompt = (
        "You are helping a candidate fill the 'Key Skills' field of a job application "
        "so it matches the job description as closely as possible for ATS/recruiter scanning. "
        "Rules:\n"
        "- ONLY use skills from the candidate's REAL skill list below. Never add a skill, "
        "tool, or technology that isn't in that list, even if the job description asks for it.\n"
        "- Reorder the candidate's real skills so the ones the job description explicitly "
        "mentions or implies come first.\n"
        "- If the job description uses different wording for a skill the candidate already "
        "has (e.g. JD says 'Amazon Web Services' and candidate has 'AWS'), you may rewrite that "
        "skill to match the JD's exact wording - but only for skills genuinely already possessed.\n"
        "- Respond ONLY with compact JSON: {\"skills\": \"comma, separated, list\"}.\n\n"
        f"CANDIDATE'S REAL SKILLS:\n{known_skills}\n\n"
        f"JOB DESCRIPTION:\n{jd_text[:3000]}"
    )
    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=400,
            reasoning_effort="low",
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        if not raw:
            raise ValueError("empty response from model")
        data = _extract_json_object(raw)
        skills = str(data.get("skills", "")).strip()
        return skills if skills else fallback_skills
    except Exception as e:
        print(f"[AI] tailor_skills_for_jd failed, using default skills: {e}")
        return fallback_skills


def compose_cold_email(job_title, company, jd_text="", profile=None):
    """Drafts a short, genuine-sounding cold outreach email to a recruiter
    whose contact address was found directly in a job posting. Returns
    {"subject": str, "body_html": str} grounded strictly in the real
    profile, or None if AI is unavailable (caller should skip sending
    rather than fabricate a template with made-up claims)."""
    profile = profile if profile is not None else load_profile()
    client = _get_client()
    if client is None:
        return None

    name = profile.get("name", "")
    prompt = (
        f"Write a short, genuine, human-sounding cold outreach email from a job candidate "
        f"named {name} to a recruiter/HR contact for the role of '{job_title}' at '{company}'. "
        "The candidate already submitted a formal application through Naukri.com; this email "
        "is a polite follow-up to stand out, not a duplicate application. Rules:\n"
        "- 3-5 short sentences. Plain, conversational, no corporate buzzwords "
        "(\"passionate\", \"leverage\", \"proven track record\", \"synergy\").\n"
        "- Mention the exact role and company by name.\n"
        "- Reference 1-2 REAL, specific highlights from the background below that are most "
        "relevant to this JD - never invent employers, numbers, or skills not listed.\n"
        "- End by saying the resume is attached and inviting them to reach out.\n"
        "- Respond ONLY with compact JSON: {\"subject\": \"<short subject line>\", \"body\": \"<email body, "
        "use \\n for line breaks, no HTML>\"}.\n\n"
        f"CANDIDATE BACKGROUND:\n{_profile_summary_text(profile)}\n\n"
        f"JOB DESCRIPTION (for context, may be partial):\n{(jd_text or '')[:2000]}"
    )
    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=500,
            reasoning_effort="low",
            response_format={"type": "json_object"},
        )
        raw = (resp.choices[0].message.content or "").strip()
        if not raw:
            raise ValueError("empty response from model")
        data = _extract_json_object(raw)
        subject = str(data.get("subject", "")).strip() or f"Application follow-up: {job_title} at {company}"
        body = str(data.get("body", "")).strip()
        if not body:
            return None
        body_html = body.replace("\n", "<br/>")
        return {"subject": subject, "body_html": body_html}
    except Exception as e:
        print(f"[AI] compose_cold_email failed: {e}")
        return None


def summarize_jd(jd_text):
    """Returns a short bullet-point summary string (HTML <br/>-separated)."""
    client = _get_client()
    if client is None or not jd_text or jd_text.strip() in ("Not available", ""):
        return None

    prompt = (
        "Summarize this job description into at most 4 short bullet points "
        "covering: key responsibilities, must-have skills, experience level, "
        "and any red flags (e.g. vague description, staffing agency). "
        "Respond with plain text bullet points starting with '- ', no preamble.\n\n"
        f"{jd_text[:3000]}"
    )
    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=250,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[AI] summarize_jd failed: {e}")
        return None
