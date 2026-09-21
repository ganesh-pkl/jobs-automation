"""Build an allowlisted ZIP; never copy credentials, account data, or Git history."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    'LICENSE', 'README.md', 'AGENTS.md', 'CLAUDE.md', '.gitignore', '.env.example',
    'profile.example.yaml', 'requirements.txt', 'naukri.py', 'naukri_apply.py',
    'login_capture.py', 'linkedin_apply.py', 'naukri_refresh_resume.py',
    'common/profile.py', 'common/llm.py', 'common/env.py', 'common/gemini.py',
    'common/groq_llm.py', 'common/human_input.py', 'common/learned_answers.py',
    'docs/USER_GUIDE.md', 'docs/AI_PROMPTS.md', 'docs/VALIDATION.md',
    'scripts/package_share.py', 'tests/test_safety.py', 'tests/test_portability.py',
    '.github/workflows/tests.yml',
    'scripts/build_pdf_guide.py',
    'output/pdf/Naukri_Automation_Complete_Guide.pdf',
)


def build():
    missing = [name for name in FILES if not (ROOT / name).is_file()]
    if missing:
        raise FileNotFoundError(f'Missing release files: {missing}')
    destination = ROOT / 'dist' / 'naukri-automation-share.zip'
    destination.parent.mkdir(exist_ok=True)
    with ZipFile(destination, 'w', ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / name, f'naukri-automation/{name}')
    print(destination)
    return destination


if __name__ == '__main__':
    build()
