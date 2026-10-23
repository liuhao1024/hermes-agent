/**
 * Unit tests for the outbound phone-number JID resolver (#143578).
 *
 * Baileys 7 drops sends to never-contacted `<digits>@s.whatsapp.net` JIDs
 * while still returning a message id; resolving through onWhatsApp() first
 * both makes the first send deliverable and fails loudly for numbers with
 * no WhatsApp account. These tests avoid importing bridge.js because that
 * file starts an HTTP server and Baileys socket at module load.
 */

import { strict as assert } from 'node:assert';

import { createOutboundJidResolver } from './bridge_helpers.js';

// -- non phone-number targets pass through without a lookup ----------------
{
  const lookedUp = [];
  const resolve = createOutboundJidResolver(
    async (number) => { lookedUp.push(number); return [{ exists: true, jid: `${number}@s.whatsapp.net` }]; },
  );

  assert.equal(await resolve('120363001234567890@g.us'), '120363001234567890@g.us');
  assert.equal(await resolve('9876543210987654@lid'), '9876543210987654@lid');
  assert.equal(await resolve('xyz-abc@newsletter'), 'xyz-abc@newsletter');
  assert.equal(await resolve(undefined), undefined);
  assert.deepEqual(lookedUp, []);

  console.log('  ✓ group/LID/newsletter targets bypass the onWhatsApp lookup');
}

// -- a never-contacted number resolves to the JID WhatsApp reports ---------
{
  const lookedUp = [];
  const resolve = createOutboundJidResolver(async (number) => {
    lookedUp.push(number);
    // Brazilian accounts: the registered JID keeps the leading 9 the caller
    // omitted — the resolver must return the registered form, not the input.
    if (number === '551187654321') return [{ exists: true, jid: '5511987654321@s.whatsapp.net' }];
    return [];
  });

  const jid = await resolve('551187654321@s.whatsapp.net');
  assert.equal(lookedUp.length, 1);
  assert.equal(jid, '5511987654321@s.whatsapp.net');

  console.log('  ✓ first send to a never-contacted number resolves via onWhatsApp()');
}

// -- the second send to the same number is served from the cache -----------
{
  const lookedUp = [];
  const resolve = createOutboundJidResolver(async (number) => {
    lookedUp.push(number);
    return [{ exists: true, jid: `${number}@s.whatsapp.net` }];
  });

  await resolve('15551234567@s.whatsapp.net');
  await resolve('15551234567@s.whatsapp.net');
  assert.equal(lookedUp.length, 1);

  console.log('  ✓ resolved numbers are cached for subsequent sends');
}

// -- a number without a WhatsApp account fails loudly ----------------------
{
  const resolve = createOutboundJidResolver(async () => [{ exists: false, jid: '15550000000@s.whatsapp.net' }]);

  await assert.rejects(
    () => resolve('15550000000@s.whatsapp.net'),
    /The number \+15550000000 has no WhatsApp account/,
  );

  console.log('  ✓ unregistered numbers raise instead of returning a false success');
}

// -- a transient lookup failure downgrades to a pass-through send ----------
{
  const logs = [];
  const resolve = createOutboundJidResolver(
    async () => { throw new Error('usync timeout'); },
    { log: (line) => logs.push(line) },
  );

  const chatId = '15559999999@s.whatsapp.net';
  assert.equal(await resolve(chatId), chatId);
  assert.equal(logs.length, 1);
  assert.match(logs[0], /onWhatsApp lookup failed for \+15559999999/);

  console.log('  ✓ lookup errors fall back to the caller JID instead of blocking the send');
}

// -- device-qualified and non-numeric local parts pass through -------------
{
  const lookedUp = [];
  const resolve = createOutboundJidResolver(
    async (number) => { lookedUp.push(number); return [{ exists: true, jid: `${number}@s.whatsapp.net` }]; },
  );

  assert.equal(await resolve('not-a-number@s.whatsapp.net'), 'not-a-number@s.whatsapp.net');
  assert.deepEqual(lookedUp, []);

  console.log('  ✓ non-numeric phone-number JIDs are not looked up');
}

// -- the cache stays bounded ------------------------------------------------
{
  let lookups = 0;
  const resolve = createOutboundJidResolver(
    async (number) => { lookups += 1; return [{ exists: true, jid: `${number}@s.whatsapp.net` }]; },
    { maxCacheEntries: 2 },
  );

  await resolve('100@s.whatsapp.net');
  await resolve('200@s.whatsapp.net');
  await resolve('300@s.whatsapp.net');
  await resolve('200@s.whatsapp.net');
  assert.equal(lookups, 3, '200 must still be cached after 100 and 300 evictions');

  console.log('  ✓ the number→JID cache evicts oldest entries past its cap');
}
