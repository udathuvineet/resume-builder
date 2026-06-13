import json
import os
import re
from typing import Generator

import anthropic
import openai as _openai_module

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
MODEL = "claude-opus-4-8"

_openai_client: _openai_module.OpenAI | None = None


def _get_openai() -> _openai_module.OpenAI:
    global _openai_client
    if _openai_client is None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise EnvironmentError("OPENAI_API_KEY is not set.")
        _openai_client = _openai_module.OpenAI(api_key=key)
    return _openai_client

_ANALYSIS_SYSTEM = """You are an expert resume optimizer and ATS specialist. Analyze the provided resume(s) against the job description.

Return ONLY a valid JSON object with this exact structure (no markdown fences, no explanation):
{
  "requirements": [
    {
      "text": "clear requirement description",
      "category": "technical|experience|education|soft_skill|certification",
      "match_score": 0.85,
      "match_detail": "specific explanation of how well the resume matches this requirement"
    }
  ],
  "overall_score": 0.75
}

Extract ALL requirements from the job description. Score 0.0 (no match) to 1.0 (perfect match)."""

_SUGGESTIONS_SYSTEM = """You are an expert resume writer and ATS optimizer. Generate specific, actionable suggestions to improve resume matches.

Return ONLY a valid JSON object (no markdown fences):
{
  "suggestions_by_requirement": {
    "<requirement_id>": [
      {
        "type": "MODIFY",
        "original_text": "exact existing text from resume to replace",
        "suggested_text": "improved version with relevant keywords and metrics",
        "section": "Experience|Skills|Summary|Education|Projects"
      },
      {
        "type": "ADD",
        "original_text": null,
        "suggested_text": "new content to add to the resume",
        "section": "Skills"
      }
    ]
  }
}

Use the exact requirement IDs provided. Be specific and ATS-optimized."""

_AUDIT_SYSTEM = """You are an expert resume coach. Given a resume and a job description, evaluate every bullet point and statement in the resume.

For each item decide:
- "rephrase": content is relevant but generic, vague, or not worded to match this JD's language/priorities
- "remove": content is irrelevant to this role and wastes space

Only return items that need attention — skip anything that is strong and well-targeted.

Return ONLY valid JSON (no markdown fences):
{
  "audit": [
    {
      "section": "Experience | Skills | Summary | Education | etc.",
      "text": "exact text of the bullet or statement from the resume",
      "verdict": "rephrase | remove",
      "reason": "specific explanation of the problem and what to do instead"
    }
  ]
}"""

_GENERATION_SYSTEM = """You are an expert resume writer. Rewrite the provided resume incorporating all suggested improvements.

Output plain text only — no markdown, no asterisks, no hashtags, no explanation.

Strict format:
1. Line 1: Full name only
2. Line 2: contact info separated by  |  (use the same separator as the original)
3. Blank line
4. Section headers in ALL CAPS on their own line (PROFESSIONAL SUMMARY, EXPERIENCE, TECHNICAL SKILLS, EDUCATION, etc.)
5. Within EXPERIENCE:
   - Employer name alone on its own line (e.g.  Intuit Inc.)
   - Job title and dates on the very next line using this exact pattern:  Title | MM/YYYY – MM/YYYY  or  Title | MM/YYYY – Present
   - Bullet points starting with •  (never use - or * for bullets)
6. One blank line between major sections
7. Keep all other sections (summary, skills, education) as they appear in the original"""


def analyze_resume(resume_texts: list[str], jd_text: str, projects_texts: list[str]) -> dict:
    resume_block = "\n\n---\n\n".join(f"[Resume {i+1}]\n{t}" for i, t in enumerate(resume_texts))
    projects_block = "\n\n---\n\n".join(projects_texts) if projects_texts else "None provided."

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=_ANALYSIS_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Job Description:\n{jd_text}\n\n"
                f"Resume(s):\n{resume_block}\n\n"
                f"Additional Projects/Context:\n{projects_block}\n\n"
                "Analyze and return JSON."
            )
        }]
    )
    return _parse_json(response.content[0].text)


def generate_suggestions(requirements: list[dict], resume_texts: list[str]) -> dict:
    resume_block = "\n\n---\n\n".join(resume_texts)
    reqs_block = "\n\n".join(
        f"ID: {r['id']}\nRequirement: {r['text']}\nScore: {r['match_score']:.0%}\nGap: {r['match_detail']}"
        for r in requirements
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=_SUGGESTIONS_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Requirements needing improvement:\n{reqs_block}\n\n"
                f"Resume:\n{resume_block}\n\n"
                "Generate suggestions and return JSON."
            )
        }]
    )
    return _parse_json(response.content[0].text)


def audit_resume_content(resume_text: str, jd_text: str) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=_AUDIT_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Job Description:\n{jd_text}\n\n"
                f"Resume:\n{resume_text}\n\n"
                "Audit every bullet and statement. Return only items needing attention."
            )
        }]
    )
    return _parse_json(response.content[0].text)


def stream_resume_generation(
    original_resume: str,
    accepted_suggestions: list[dict],
    profile: dict | None = None,
) -> Generator[str, None, None]:
    improvements = "\n\n".join(
        f"{'MODIFY' if s['type'] == 'MODIFY' else 'ADD'} in {s.get('section', 'resume')}:\n"
        + (f"Original: {s['original_text']}\n" if s.get('original_text') else "")
        + f"New: {s.get('edited_text') or s['suggested_text']}"
        for s in accepted_suggestions
    )

    contact_note = ""
    if profile:
        name = profile.get("name", "").strip()
        contact_parts = [v.strip() for v in [
            profile.get("email"), profile.get("phone"),
            profile.get("location"), profile.get("linkedin"),
        ] if v and v.strip()]
        if name or contact_parts:
            contact_note = "\n\nIMPORTANT — use exactly this header (two lines, centered):"
            if name:
                contact_note += f"\nLine 1: {name}"
            if contact_parts:
                contact_note += (
                    f"\nLine 2: {' · '.join(contact_parts)}"
                    "\n(Replace · with the same separator used in the original resume if different)"
                )

    prompt = (
        f"Original Resume:\n{original_resume}\n\n"
        f"Improvements to apply:\n{improvements}"
        f"{contact_note}\n\n"
        "Write the complete updated resume."
    )

    with client.messages.stream(
        model=MODEL,
        max_tokens=4096,
        system=_GENERATION_SYSTEM,
        messages=[{"role": "user", "content": prompt}]
    ) as stream:
        for text in stream.text_stream:
            yield text


_REFINE_SYSTEM = """You are a senior resume consultant and hiring manager who is critically reviewing AI-generated resume suggestions.

Your job: for each suggestion, decide whether it is:
- "approved"  — already strong, specific, and well-targeted; keep Claude's version
- "improved"  — directionally right but needs sharper wording, better metrics, more ATS-specific language, or stronger action verbs; provide a better version
- "flagged"   — generic filler, unrealistic claim, doesn't actually address the gap, or could hurt the candidate

Be honest and critical. Approve sparingly — most suggestions benefit from tightening.

Return ONLY valid JSON (no markdown fences):
{
  "refinements": [
    {
      "suggestion_id": "<id>",
      "verdict": "approved | improved | flagged",
      "improved_text": "your rewritten version (only when verdict is improved, else null)",
      "critique": "one or two sentences explaining your decision and what you changed or why it was flagged"
    }
  ]
}"""


def refine_suggestions_with_gpt4(
    resume_text: str,
    jd_text: str,
    suggestions: list[dict],
) -> dict:
    oai = _get_openai()

    sugg_block = "\n\n".join(
        f"ID: {s['id']}\n"
        f"Section: {s.get('section') or 'General'}\n"
        f"Type: {s['type']}\n"
        f"Requirement gap: {s.get('requirement_text', '')}\n"
        + (f"Original text in resume: {s['original_text']}\n" if s.get('original_text') else "")
        + f"Claude's suggestion: {s.get('edited_text') or s['suggested_text']}"
        for s in suggestions
    )

    response = oai.chat.completions.create(
        model="gpt-4o",
        max_tokens=4096,
        messages=[
            {"role": "system", "content": _REFINE_SYSTEM},
            {"role": "user", "content": (
                f"Job Description:\n{jd_text}\n\n"
                f"Original Resume:\n{resume_text}\n\n"
                f"Suggestions to review:\n{sugg_block}\n\n"
                "Review every suggestion and return JSON."
            )},
        ],
    )
    return _parse_json(response.choices[0].message.content)


def _parse_json(text: str) -> dict:
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if match:
        return json.loads(match.group(1))
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No valid JSON found in response: {text[:300]}")
