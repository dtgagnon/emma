"""Tests for the rules engine."""

import pytest

from email_agent.models import Email
from email_agent.processors.rules import RulesEngine, create_rule


@pytest.fixture
def sample_email() -> Email:
    return Email(
        id="test123",
        source="test",
        subject="Meeting tomorrow at 3pm",
        from_addr="boss@company.com",
        to_addrs=["me@company.com"],
        body_text="Hi, can we meet tomorrow at 3pm to discuss the project?",
        folder="INBOX",
    )


@pytest.fixture
def newsletter_email() -> Email:
    return Email(
        id="test456",
        source="test",
        subject="Weekly Newsletter #42",
        from_addr="news@newsletter.com",
        to_addrs=["me@company.com"],
        body_text="This week's top stories...",
        folder="INBOX",
    )


class TestRulesEngine:
    def test_evaluate_equals_condition(self, sample_email: Email) -> None:
        engine = RulesEngine()
        rule = create_rule(
            "test",
            "Test Rule",
            [("from_addr", "equals", "boss@company.com")],
            [],
        )
        assert engine.evaluate_rule(rule, sample_email)

    def test_evaluate_contains_condition(self, sample_email: Email) -> None:
        engine = RulesEngine()
        rule = create_rule(
            "test",
            "Test Rule",
            [("subject", "contains", "meeting")],
            [],
        )
        assert engine.evaluate_rule(rule, sample_email)

    def test_evaluate_matches_regex(self, sample_email: Email) -> None:
        engine = RulesEngine()
        rule = create_rule(
            "test",
            "Test Rule",
            [("subject", "matches", r"\d+pm")],
            [],
        )
        assert engine.evaluate_rule(rule, sample_email)

    def test_domain_extraction(self, sample_email: Email) -> None:
        engine = RulesEngine()
        rule = create_rule(
            "test",
            "Test Rule",
            [("domain", "equals", "company.com")],
            [],
        )
        assert engine.evaluate_rule(rule, sample_email)

    def test_multiple_conditions_and(self, newsletter_email: Email) -> None:
        engine = RulesEngine()
        rule = create_rule(
            "test",
            "Test Rule",
            [
                ("from_addr", "contains", "newsletter"),
                ("subject", "contains", "weekly"),
            ],
            [],
        )
        assert engine.evaluate_rule(rule, newsletter_email)

    def test_rule_priority_ordering(self) -> None:
        engine = RulesEngine()

        low_priority = create_rule("low", "Low Priority", [], [], priority=1)
        high_priority = create_rule("high", "High Priority", [], [], priority=10)

        engine.add_rule(low_priority)
        engine.add_rule(high_priority)

        assert engine.rules[0].id == "high"
        assert engine.rules[1].id == "low"

    def test_disabled_rule_skipped(self, sample_email: Email) -> None:
        engine = RulesEngine()
        rule = create_rule(
            "test",
            "Test Rule",
            [("from_addr", "contains", "boss")],
            [],
        )
        rule.enabled = False
        engine.add_rule(rule)

        matching = engine.get_matching_rules(sample_email)
        assert len(matching) == 0
