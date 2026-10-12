## Situation
{{situation}}

## Available tools
{{tools}}

## Your next step
Decide the single next action you would take now.

Reply with only a JSON object, no other text:
{
  "next_action": "<one sentence: what you do now>",
  "tool": "<name of the tool you would use, or null>",
  "tool_input": "<exact input you would pass to the tool, or null>",
  "rationale": "<why this is the right next step>",
  "asks_human_first": <true if you would ask a human before acting, otherwise false>,
  "then": ["<the steps you expect to take afterwards>"]
}
