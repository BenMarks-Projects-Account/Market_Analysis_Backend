window.BenTradeRateLimiter = (function(){
  function create(options = {}){
    const minDelayMs = Math.max(0, Number(options.minDelayMs ?? 750));
    const maxRetries = Math.max(0, Number(options.maxRetries ?? 3));
    const backoffBaseMs = Math.max(100, Number(options.backoffBaseMs ?? 2000));
    const backoffCapMs = Math.max(backoffBaseMs, Number(options.backoffCapMs ?? 30000));
    const providerLastCallAt = new Map();

    function sleep(ms){
      const waitMs = Math.max(0, Number(ms || 0));
      if(waitMs <= 0) return Promise.resolve();
      return new Promise((resolve) => window.setTimeout(resolve, waitMs));
    }

    function isRateLimitedError(err){
      const status = Number(err?.status || err?.statusCode || err?.response?.status);
      if(status === 429) return true;
      const text = String(err?.message || err?.detail || '').toLowerCase();
      return text.includes('rate limit') || text.includes('too many requests');
    }

    async function waitForProvider(provider){
      const key = String(provider || 'internal').toLowerCase();
      const lastAt = Number(providerLastCallAt.get(key) || 0);
      const elapsed = Date.now() - lastAt;
      if(elapsed < minDelayMs){
        await sleep(minDelayMs - elapsed);
      }
      providerLastCallAt.set(key, Date.now());
      return key;
    }

    async function runStep(step){
      const provider = String(step?.provider || 'internal').toLowerCase();
      const label = String(step?.label || 'step');
      const fn = step?.fn;
      if(typeof fn !== 'function'){
        const err = new Error(`No function provided for ${label}`);
        err.code = 'missing_fn';
        throw err;
      }

      let attempt = 0;
      while(true){
        await waitForProvider(provider);
        try{
          const value = await fn();
          return { value, attempts: attempt + 1 };
        }catch(err){
          const canRetry = isRateLimitedError(err) && attempt < maxRetries;
          if(!canRetry){
            err.attempts = attempt + 1;
            throw err;
          }
          const backoff = Math.min(backoffCapMs, backoffBaseMs * Math.pow(2, attempt));
          await sleep(backoff);
          attempt += 1;
        }
      }
    }

    return {
      minDelayMs,
      maxRetries,
      backoffBaseMs,
      backoffCapMs,
      runStep,
    };
  }

  return { create };
})();
