import os

# Always run tests in sandbox mode to use mock providers instead of real endpoints.
os.environ["AGENT_X_SANDBOX"] = "1"

# Clear provider registry just in case it was bootstrapped before this
from agentx.execution import providers
providers.clear()

