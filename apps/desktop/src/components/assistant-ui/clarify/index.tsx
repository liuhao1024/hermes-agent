'use client'

import { type ToolCallMessagePartProps, useAuiState } from '@assistant-ui/react'
import { useStore } from '@nanostores/react'
import { useMemo, useState } from 'react'

import { useSessionView } from '@/app/chat/session-view'
import { ToolFallback } from '@/components/assistant-ui/tool/fallback'
import { $settledClarifyResults, type ClarifyRequest, sessionClarifyRequest } from '@/store/clarify'

import { selectMessageRunning } from '../tool/fallback-model'

import { type ClarifyArgs, readClarifyArgs } from './parse'
import { ClarifyToolPending } from './pending'
import { ClarifyToolSettled } from './settled'
import { useUndeliveredClarify } from './use-undelivered'

export const ClarifyTool = (props: ToolCallMessagePartProps) => {
  // Answered → settled Q&A (ToolFallback collapsed the answer away).
  if (props.result !== undefined) {
    return <ClarifyToolSettled {...props} />
  }

  return <ClarifyToolLive {...props} />
}

// The request each tool row asked, remembered past its clearing and past a
// remount (a session switch, a stopped turn): a skip or a typed answer settles
// it in the store before (or without) `tool.complete`. Keyed by session,
// message and call: some providers name every call "call_0", and a bare call id
// would show a later card the answer of an earlier one.
const requestIdByToolCall = new Map<string, string>()

// Whether this row may follow the session's live request: only when its own
// args cover every question the request asks. A one-at-a-time batch re-arms
// the same row for its next question, so the row must keep up — but a
// duplicate row left behind by a re-delivered activate must not paint the
// current question as a second live card. No request means nothing to follow.
function rowCoversRequest(request: ClarifyRequest | null, args: ClarifyArgs): boolean {
  if (!request) {
    return true
  }

  const asked = new Set((args.questions ?? []).map(entry => entry.question.trim()))

  return asked.size > 0 && request.questions.every(question => asked.has(question.question.trim()))
}

function ClarifyToolLive(props: ToolCallMessagePartProps) {
  // The tool row is in whichever session's transcript rendered it — read THAT
  // session's clarify (primary or tile), not the globally-active one.
  const sessionId = useStore(useSessionView().$runtimeId)
  const $request = useMemo(() => sessionClarifyRequest(sessionId), [sessionId])
  const request = useStore($request)
  const fromArgs = useMemo(() => readClarifyArgs(props.args), [props.args])
  const messageRunning = useAuiState(selectMessageRunning)
  const messageId = useAuiState(s => s.message.id)
  const rowKey = `${sessionId ?? ''}:${messageId}:${props.toolCallId}`
  // Answering clears the request a beat before `tool.complete` swaps in the
  // settled card. Latch submit so that gap doesn't demote; Stop also clears
  // the request and must still collapse an unanswered card.
  const [answered, setAnswered] = useState(false)
  const settledResults = useStore($settledClarifyResults)
  const requestId = requestIdByToolCall.get(rowKey)
  const settledResult = requestId && request?.requestId !== requestId ? settledResults[requestId] : undefined
  const covers = rowCoversRequest(request, fromArgs)

  if (request && !settledResult && request.requestId !== requestId && covers) {
    requestIdByToolCall.set(rowKey, request.requestId)
  }

  const undelivered = useUndeliveredClarify(sessionId, messageRunning && !request && !answered && !settledResult)

  if (settledResult) {
    return <ClarifyToolSettled {...props} result={settledResult} />
  }

  // A row that does not carry the live question (a re-delivered duplicate, an
  // earlier call) is history: it renders its own args as a plain tool row,
  // never as the session's one interactive card.
  if (request && !covers && !answered) {
    return <ToolFallback {...props} />
  }

  // Stopped mid-prompt with no result — don't leave a dead interactive panel.
  // `session.info` reports running=false while clarify is blocking, so the
  // running flag alone would remount the question as a tool row. Keep the
  // card while a request is open or this instance already submitted.
  if (!messageRunning && !request && !answered) {
    return <ToolFallback {...props} />
  }

  return (
    <ClarifyToolPending
      fromArgs={fromArgs}
      onAnswered={() => setAnswered(true)}
      request={request}
      undelivered={undelivered}
    />
  )
}
