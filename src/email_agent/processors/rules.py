"""Rule-based email processing engine."""

import fnmatch
import re
from typing import Any, Callable

from email_agent.models import (
    Email,
    PlannedAction,
    ProcessingResult,
    Rule,
    RuleAction,
    RuleCondition,
)


class RulesEngine:
    """Engine for evaluating and executing email rules."""

    def __init__(self) -> None:
        self.rules: list[Rule] = []
        self._action_handlers: dict[str, Callable] = {}

        # Register built-in action handlers
        self._register_builtin_handlers()

    def _register_builtin_handlers(self) -> None:
        """Register built-in action handlers."""
        # Actions are registered but execution depends on email source capabilities
        pass

    def add_rule(self, rule: Rule) -> None:
        """Add a rule to the engine."""
        self.rules.append(rule)
        # Sort by priority (higher first)
        self.rules.sort(key=lambda r: r.priority, reverse=True)

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID."""
        for i, rule in enumerate(self.rules):
            if rule.id == rule_id:
                self.rules.pop(i)
                return True
        return False

    def register_action_handler(
        self, action_type: str, handler: Callable[[Email, dict[str, Any]], Any]
    ) -> None:
        """Register a custom action handler."""
        self._action_handlers[action_type] = handler

    def evaluate_condition(self, condition: RuleCondition, email: Email) -> bool:
        """Evaluate a single condition against an email."""
        # Get the field value from the email
        field_value = self._get_field_value(email, condition.field)
        if field_value is None:
            return False

        operator = condition.operator.lower()
        expected = condition.value

        if operator == "equals":
            return str(field_value).lower() == str(expected).lower()

        elif operator == "contains":
            return str(expected).lower() in str(field_value).lower()

        elif operator == "starts_with":
            return str(field_value).lower().startswith(str(expected).lower())

        elif operator == "ends_with":
            return str(field_value).lower().endswith(str(expected).lower())

        elif operator == "matches":
            # Regex match
            try:
                return bool(re.search(str(expected), str(field_value), re.IGNORECASE))
            except re.error:
                return False

        elif operator == "glob":
            # Glob/wildcard match
            return fnmatch.fnmatch(str(field_value).lower(), str(expected).lower())

        elif operator == "in":
            # Value is in a list
            if isinstance(expected, list):
                return str(field_value).lower() in [str(v).lower() for v in expected]
            return False

        elif operator == "not_in":
            if isinstance(expected, list):
                return str(field_value).lower() not in [str(v).lower() for v in expected]
            return True

        elif operator == "exists":
            return bool(field_value)

        elif operator == "not_exists":
            return not bool(field_value)

        return False

    def _get_field_value(self, email: Email, field: str) -> Any:
        """Get a field value from an email, supporting nested paths."""
        # Handle special computed fields
        if field == "domain":
            # Extract domain from from_addr
            if "@" in email.from_addr:
                return email.from_addr.split("@")[-1].rstrip(">")
            return None

        if field == "has_attachments":
            return len(email.attachments) > 0

        if field == "attachment_count":
            return len(email.attachments)

        # Handle direct fields
        if hasattr(email, field):
            return getattr(email, field)

        # Handle header lookups
        if field.startswith("header."):
            header_name = field[7:]
            return email.headers.get(header_name)

        return None

    def evaluate_rule(self, rule: Rule, email: Email) -> bool:
        """Evaluate if all conditions of a rule match an email."""
        if not rule.enabled:
            return False

        if not rule.conditions:
            return True  # No conditions = always match

        # All conditions must match (AND logic)
        return all(self.evaluate_condition(cond, email) for cond in rule.conditions)

    def get_matching_rules(self, email: Email) -> list[Rule]:
        """Get all rules that match an email."""
        return [rule for rule in self.rules if self.evaluate_rule(rule, email)]

    async def process_email(
        self,
        email: Email,
        action_executor: Callable[[Email, RuleAction], Any] | None = None,
        *,
        dry_run: bool = False,
    ) -> ProcessingResult:
        """Process an email through all matching rules.

        Args:
            email: The email to process
            action_executor: Optional callback to execute actions
                             Signature: (email, action) -> result
            dry_run: If True, only report planned actions without executing them

        Returns:
            ProcessingResult with actions taken (or planned) and any errors
        """
        result = ProcessingResult(
            email_id=email.id,
            email_subject=email.subject,
            dry_run=dry_run,
        )

        matching_rules = self.get_matching_rules(email)

        for rule in matching_rules:
            result.rules_matched.append(rule.id)

            for action in rule.actions:
                if dry_run:
                    # In dry-run mode, record the planned action without executing
                    planned = PlannedAction(
                        rule_id=rule.id,
                        rule_name=rule.name,
                        action_type=action.type,
                        params=action.params,
                        description=self._describe_action(action),
                    )
                    result.planned_actions.append(planned)
                else:
                    # Execute the action
                    try:
                        if action_executor:
                            await action_executor(email, action)
                        elif action.type in self._action_handlers:
                            handler = self._action_handlers[action.type]
                            handler(email, action.params)

                        result.actions_taken.append(f"{rule.id}:{action.type}")
                    except Exception as e:
                        result.errors.append(f"{rule.id}:{action.type}: {e}")
                        result.success = False

        return result

    def _describe_action(self, action: RuleAction) -> str:
        """Generate a human-readable description of an action."""
        if action.type == "move":
            folder = action.params.get("folder", "?")
            return f"move to {folder}"
        elif action.type == "delete":
            return "delete (move to Trash)"
        elif action.type == "flag":
            flag = action.params.get("flag", "flagged")
            return f"add flag '{flag}'"
        elif action.type == "unflag":
            flag = action.params.get("flag", "flagged")
            return f"remove flag '{flag}'"
        elif action.type == "archive":
            return "archive"
        elif action.type == "label":
            label = action.params.get("label", "?")
            return f"apply label '{label}'"
        elif action.type == "llm_process":
            return "process with LLM"
        else:
            return f"{action.type}: {action.params}"


def create_rule(
    rule_id: str,
    name: str,
    conditions: list[tuple[str, str, Any]],
    actions: list[tuple[str, dict[str, Any]]],
    priority: int = 0,
    description: str = "",
) -> Rule:
    """Helper to create a rule from simple tuples.

    Args:
        rule_id: Unique identifier for the rule
        name: Human-readable name
        conditions: List of (field, operator, value) tuples
        actions: List of (action_type, params_dict) tuples
        priority: Higher priority rules run first
        description: Optional description

    Example:
        rule = create_rule(
            "spam_filter",
            "Filter Spam",
            [("from_addr", "contains", "promo@"), ("subject", "matches", r"(?i)buy now")],
            [("move", {"folder": "Spam"})],
            priority=100,
        )
    """
    return Rule(
        id=rule_id,
        name=name,
        description=description,
        priority=priority,
        conditions=[
            RuleCondition(field=field, operator=op, value=val)
            for field, op, val in conditions
        ],
        actions=[RuleAction(type=action_type, params=params) for action_type, params in actions],
    )
