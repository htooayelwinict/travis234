# References

- OpenAI function calling guide: `https://platform.openai.com/docs/guides/function-calling`
- Context7 `/openai/openai-python` notes: strict function tools, JSON schema
  parameters, Pydantic tool helpers, and multiple tool calls in one response.

Relevant takeaway: prefer named tools with narrow JSON inputs over asking the
model to compose shell snippets. The runtime should execute application-owned
functions and feed structured observations back to the model.

