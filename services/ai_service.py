import json
import os
import re
from typing import Generator

import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
MODEL = "claude-opus-4-8"

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

_GENERATION_SYSTEM = """You are an expert resume writer. Rewrite the provided resume incorporating all suggested improvements.

Output the complete resume as plain text only — no preamble, no explanation, no markdown.
Format:
- Name and contact on first lines
- Section headers in ALL CAPS (e.g. EXPERIENCE, SKILLS, EDUCATION)
- Bullet points using the • character
- One blank line between sections"""


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
        parts = [v for v in [
            profile.get("name"), profile.get("email"), profile.get("phone"),
            profile.get("location"), profile.get("linkedin"),
        ] if v]
        if parts:
            contact_note = (
                f"\n\nIMPORTANT: The resume header must include exactly this contact line "
                f"(all on one line, separated by  |  ):\n"
                f"{' | '.join(parts)}"
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


def _parse_json(text: str) -> dict:
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if match:
        return json.loads(match.group(1))
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No valid JSON found in response: {text[:300]}")
