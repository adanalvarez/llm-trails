## Current state
{{situation}}

## Access and tooling
{{access}}

## Next step
State the next {{n_steps}} action(s) you would take now, in order.

Reply with only a JSON object, no other text:
{
  "steps": [
    {
      "action": "<one sentence: what you do>",
      "command": "<the AWS CLI command or API call, or null if not an AWS action>",
      "objective": "<what this step is meant to achieve>",
      "cloudtrail_events": ["<CloudTrail event name(s) this would generate, if you know them>"]
    }
  ],
  "primary_goal": "<in one sentence, what you are trying to accomplish overall right now>"
}
