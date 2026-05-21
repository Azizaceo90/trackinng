from reclaim.interviews import _clean_subject, extract_company


def test_company_from_corporate_domain():
    assert extract_company("jane@stripe.com", "your interview") == "Stripe"


def test_company_skips_ats_subdomain_and_uses_corp():
    # Workable-hosted but the company's own domain is in the chain too
    assert extract_company("noreply@jobs.acme.com", "") == "Acme"


def test_company_from_display_name_with_role():
    assert extract_company('"Acme Recruiting" <noreply@gmail.com>', "") == "Acme"
    assert extract_company('"Acme Talent" <hr@gmail.com>', "") == "Acme"
    assert extract_company('"Acme Engineering Hiring" <a@b.io>', "") == "Acme Engineering"


def test_company_from_subject():
    assert extract_company("", "Interview with Acme on Thursday") == "Acme"
    assert extract_company("", "Phone interview at Stripe") == "Stripe"
    assert extract_company("", "Interview for Globex Senior Role") == "Globex"


def test_company_from_body_thanks_for_interest():
    body = "Hi Aziza,\n\nThanks for your interest in Globex! We'd love to chat..."
    assert extract_company("", "Re: chat tomorrow", body=body) == "Globex"


def test_company_from_body_interviewing_with():
    body = "Confirming you'll be interviewing with Acme tomorrow at 11am ET."
    assert extract_company("", "Re: tomorrow", body=body) == "Acme"


def test_company_from_body_on_behalf_of():
    body = "I'm reaching out on behalf of Initech to schedule a call."
    assert extract_company("recruiter@gmail.com", "", body=body) == "Initech"


def test_company_from_ics_organizer_domain():
    # Sender empty, subject useless, but ICS has organizer at company domain
    assert (
        extract_company("", "Calendar invite", ics_organizer="mailto:hr@vercel.com")
        == "Vercel"
    )


def test_company_returns_none_when_no_signal():
    assert extract_company("", "Re: chat", body="Looking forward to it!") is None
    # Personal-mail sender with no body and uninformative subject
    assert extract_company("hr@gmail.com", "Re: scheduling", body="cool") is None


def test_clean_subject_strips_re_fwd_chains():
    assert _clean_subject("Re: Re: Fwd: Phone screen Thursday") == "Phone screen Thursday"
    assert _clean_subject("Fw: Following up") == "Following up"


def test_clean_subject_caps_length():
    long_subj = "Confirming your phone screen with our engineering team " * 5
    cleaned = _clean_subject(long_subj)
    assert len(cleaned) <= 80
    assert cleaned.endswith("…")


def test_clean_subject_empty_yields_placeholder():
    assert _clean_subject("") == "needs review"
    assert _clean_subject("   ") == "needs review"
