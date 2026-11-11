"""Verify named routes with installed Hermes resolver and its OpenAI client."""
import json
from pathlib import Path
from hermes_cli.runtime_provider import resolve_runtime_provider
from hermes_cli.config import load_config, get_custom_provider_context_length
from openai import OpenAI

results = []
for name, model, expected_url, expected_context in [
    ('safeagents-glm', 'glm-5.3', 'http://127.0.0.1:8788/api/coding/paas/v4', 1048576),
    ('safeagents-local', 'mem-llm-large', 'http://127.0.0.1:8789/v1', 16384),
]:
    route = resolve_runtime_provider(requested=name, target_model=model)
    assert route['base_url'].rstrip('/') == expected_url, {k: route.get(k) for k in ('provider','base_url','api_mode')}
    assert route['api_mode'] == 'chat_completions', route['api_mode']
    context = get_custom_provider_context_length(model=model, base_url=route['base_url'])
    assert context == expected_context, (name, context)
    client = OpenAI(base_url=route['base_url'], api_key=route['api_key'], timeout=120)
    response = client.chat.completions.create(
        model=model, messages=[{'role':'user','content':'Call terminal with command exactly: printf safeagents-route-ok'}],
        tools=[{'type':'function','function':{'name':'terminal','description':'Run a shell command','parameters':{'type':'object','properties':{'command':{'type':'string'}},'required':['command']}}}],
        tool_choice={'type':'function','function':{'name':'terminal'}}, max_tokens=256,
    )
    calls = response.choices[0].message.tool_calls
    assert calls and calls[0].function.name == 'terminal'
    assert json.loads(calls[0].function.arguments)['command'] == 'printf safeagents-route-ok'
    result = {'provider':name,'model':model,'base_url':route['base_url'],'api_mode':route['api_mode'],'context_length':context,'tool_call':calls[0].function.model_dump(),'passed':True}
    print(json.dumps(result), flush=True)
    results.append(result)
Path(__file__).with_name('verify-hermes-routes.json').write_text(json.dumps(results, indent=2)+'\n')
