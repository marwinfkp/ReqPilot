"""Module M1 - the minimal demonstration UI (architecture ADR-008).

Server-rendered Jinja2. Exists to demonstrate the governance workflow, not to be
a product surface. Authorization is evaluated server-side on every request; the
templates hide unavailable actions as a convenience, never as the control.
"""
