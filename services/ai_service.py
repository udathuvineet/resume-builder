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

_DETAILED_REVIEW_SYSTEM = """You are an expert resume reviewer and career coach. Analyze a resume against a job description with precision and honesty.

Compare across these dimensions: Technical Skills, Experience & Responsibilities, Business Impact, Domain Knowledge, Leadership & Collaboration, Methodologies & Processes.

STRICT RULES — no exceptions:
- Do NOT fabricate experience, technologies, projects, metrics, certifications, or responsibilities
- Preserve original meaning in all modifications; only improve wording, clarity, measurable impact, and JD alignment
- For additions with no_evidence: set suggested_bullet to null
- Every recommendation must have clear evidence and reasoning

Return ONLY valid JSON (no markdown fences):
{
  "modifications": [
    {
      "current_text": "exact bullet or statement from resume",
      "suggested_revision": "revised version — stronger wording, clearer ownership, better JD alignment",
      "section": "Experience|Summary|Skills|Education|Projects",
      "gap_addressed": "specific JD requirement, keyword, or outcome this better represents",
      "evidence_type": "direct|inferred|no_evidence",
      "evidence_explanation": "one sentence: what in the resume supports this change",
      "reasoning": "why this revision is stronger — what improves: clarity / impact / alignment / keywords",
      "impact": "high|medium|low"
    }
  ],
  "additions": [
    {
      "jd_requirement": "specific requirement or skill missing or underrepresented in resume",
      "section": "Skills|Experience|Summary|Projects",
      "relevance": "high|medium|low",
      "evidence_type": "direct|inferred|no_evidence",
      "evidence_explanation": "what evidence exists in the resume — or explicitly state there is none",
      "reasoning": "why this requirement matters for the role and what gap exists in the current resume",
      "suggested_bullet": "truthful bullet text if direct or inferred evidence supports it — null if no_evidence"
    }
  ],
  "removals": [
    {
      "resume_point": "exact text of the bullet or statement to reconsider",
      "section": "Experience|Skills|Summary|Education",
      "relevance": "high|medium|low",
      "evidence_type": "direct|inferred|no_evidence",
      "evidence_explanation": "why this content is or is not relevant to the target role",
      "reasoning": "why to reconsider: redundant / outdated / unrelated / too weak",
      "suggested_action": "remove|shorten|merge"
    }
  ]
}

evidence_type definitions:
- "direct": explicitly and clearly present in the resume
- "inferred": likely true given the context but not explicitly stated
- "no_evidence": not supported by the resume — do not fabricate

impact / relevance scale:
- "high": significant effect on recruiter relevance, ATS scoring, or interview conversion
- "medium": moderate improvement
- "low": minor polish

Optimize for truthful representation of experience. Only suggest additions where the candidate plausibly has the experience."""

_AUDIT_SYSTEM = _DETAILED_REVIEW_SYSTEM  # alias kept for backward compat — not called in new flow

_GPT4_SUGGESTIONS_SYSTEM = """You are an expert resume writer and ATS optimizer. Generate specific, actionable suggestions to improve how a resume matches a job description.

You will be shown suggestions already made by another AI. Do NOT repeat or rephrase those — only add suggestions that address something genuinely new or different.

Write in clear, natural business English. Do not use:
- Em dashes (—) or en dashes (–) as prose punctuation
- Cliches like "spearheaded", "leveraged", "synergized", "passionate", "dynamic", "results-driven"
- Excessive hyphens to connect thoughts mid-sentence

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

If a requirement is already well-covered by the existing suggestions, omit it entirely.
Use the exact requirement IDs provided. Be specific and ATS-optimized."""

_GENERATION_SYSTEM = """You are an expert resume writer. Rewrite the provided resume incorporating all suggested improvements.

Output plain text only — no markdown, no asterisks, no hashtags, no explanation.

Language rules — write natural business English:
- No em dashes (—) or en dashes (–) as prose punctuation inside bullet points or summaries
- No AI cliches: spearheaded, leveraged, synergized, passionate, dynamic, results-driven, robust
- No mid-sentence hyphens connecting thoughts (e.g. avoid "led the team - resulting in")
- Use clear, direct sentences with strong action verbs (built, reduced, shipped, led, grew, cut)

Title alignment rule:
- Read the job description to identify the target role title and seniority level
- Adjust the job titles in the EXPERIENCE section to match what is most appropriate for this role
  (e.g. if the role is "Data Analyst", use "Data Analyst" not "Data Scientist" where the work fits)
- Only change titles where the underlying work genuinely matches the target role type
- Do not inflate or deflate seniority levels beyond what the content supports

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


def review_resume_detailed(
    resume_texts: list[str],
    jd_text: str,
    requirements: list[dict],
) -> dict:
    """Unified review: modifications + additions + removals with evidence and impact ratings."""
    resume_block = "\n\n---\n\n".join(f"[Resume {i+1}]\n{t}" for i, t in enumerate(resume_texts))
    reqs_block = "\n".join(
        f"- [{r.get('category', 'general')}] {r['text']} (current match: {r['match_score']:.0%})"
        for r in requirements
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=_DETAILED_REVIEW_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Job Description:\n{jd_text}\n\n"
                f"Requirements already identified (use for context):\n{reqs_block}\n\n"
                f"Resume:\n{resume_block}\n\n"
                "Perform a detailed review across all dimensions and return JSON."
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


_GPT4_ANALYSIS_SYSTEM = """You are an expert resume analyst and ATS specialist. Independently analyze the provided resume against the job description.

Return ONLY a valid JSON object (no markdown fences):
{
  "requirements": [
    {
      "req_id": "<id from the provided list>",
      "score": 0.85,
      "detail": "specific explanation of how well the resume matches this requirement"
    }
  ],
  "overall_score": 0.75,
  "summary": "2-3 sentence honest assessment of the resume's fit for this role"
}

Score 0.0 (no match) to 1.0 (perfect match). Use the exact req_id values provided."""


def analyze_resume_gpt4(requirements: list[dict], resume_text: str, jd_text: str) -> dict:
    oai = _get_openai()
    reqs_block = "\n".join(
        f"req_id: {r['id']}  |  {r['text']}" for r in requirements
    )
    response = oai.chat.completions.create(
        model="gpt-4o",
        max_tokens=4096,
        messages=[
            {"role": "system", "content": _GPT4_ANALYSIS_SYSTEM},
            {"role": "user", "content": (
                f"Job Description:\n{jd_text}\n\n"
                f"Resume:\n{resume_text}\n\n"
                f"Requirements to score (use these exact req_id values):\n{reqs_block}\n\n"
                "Score each requirement and return JSON."
            )},
        ],
    )
    return _parse_json(response.choices[0].message.content)


def generate_suggestions_gpt4(
    requirements: list[dict],
    resume_texts: list[str],
    existing_suggestions: list[dict] | None = None,
) -> dict:
    oai = _get_openai()
    resume_block = "\n\n---\n\n".join(resume_texts)
    reqs_block = "\n\n".join(
        f"ID: {r['id']}\nRequirement: {r['text']}\nScore: {r['match_score']:.0%}\nGap: {r['match_detail']}"
        for r in requirements
    )
    existing_block = ""
    if existing_suggestions:
        existing_block = "\n\nSuggestions already made by Claude (do NOT repeat these):\n" + "\n".join(
            f"- [{s.get('section','?')}] {s.get('edited_text') or s.get('suggested_text','')}"
            for s in existing_suggestions
        )
    response = oai.chat.completions.create(
        model="gpt-4o",
        max_tokens=4096,
        messages=[
            {"role": "system", "content": _GPT4_SUGGESTIONS_SYSTEM},
            {"role": "user", "content": (
                f"Requirements needing improvement:\n{reqs_block}\n\n"
                f"Resume:\n{resume_block}"
                f"{existing_block}\n\n"
                "Generate only NEW, unique suggestions and return JSON."
            )},
        ],
    )
    return _parse_json(response.choices[0].message.content)


def stream_resume_generation(
    original_resume: str,
    accepted_suggestions: list[dict],
    profile: dict | None = None,
    jd_text: str = "",
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

    jd_block = f"\n\nJob Description (for title alignment):\n{jd_text}" if jd_text else ""

    prompt = (
        f"Original Resume:\n{original_resume}\n\n"
        f"Improvements to apply:\n{improvements}"
        f"{contact_note}"
        f"{jd_block}\n\n"
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


def rephrase_bullet_for_jd(bullet_text: str, jd_text: str, section: str) -> str:
    """Rephrase an existing resume bullet to better match the target JD's language."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=200,
        system=(
            "You are a resume editor. Rephrase an existing resume bullet point to better "
            "match the language, keywords, and priorities of a specific job description. "
            "Write in clear, direct business English. "
            "No em dashes, no AI clichés, no filler words. "
            "Return ONLY the rephrased bullet — one line, no prefix, no explanation."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Section: {section}\n"
                f"Original bullet: {bullet_text}\n\n"
                f"Job Description (key context):\n{jd_text[:1500]}\n\n"
                "Rephrase the bullet to better match this role's language and priorities."
            )
        }]
    )
    return response.content[0].text.strip().lstrip("•- ").strip()


def suggest_bullet_integration(original_bullet: str, suggestion_text: str, section: str) -> str:
    """Return a single merged bullet combining original_bullet with suggestion_text."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=200,
        system=(
            "You are a resume editor. Merge new content into an existing resume bullet point. "
            "Write in clear, direct business English. "
            "No em dashes, no AI cliches, no filler words. "
            "Return ONLY the merged bullet text — one line, no prefix, no explanation."
        ),
        messages=[{
            "role": "user",
            "content": (
                f"Section: {section}\n"
                f"Existing bullet: {original_bullet}\n"
                f"Content to integrate: {suggestion_text}\n\n"
                "Write the merged bullet point."
            )
        }]
    )
    return response.content[0].text.strip().lstrip("•- ").strip()


def _parse_json(text: str) -> dict:
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if match:
        return json.loads(match.group(1))
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No valid JSON found in response: {text[:300]}")
