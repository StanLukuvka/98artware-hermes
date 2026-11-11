/**
 * art98 desktop pane: whether the Windows 98 VM is attached, and the plugin's
 * media folders. Talks to dashboard/plugin_api.py through ctx.rest.
 * Plain ESM loaded uncompiled: jsx() calls, not JSX syntax.
 */

import { Button, StatusDot, useQuery } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'art98'
const KINDS = ['recordings', 'animations', 'states', 'screenshots']

function makePane(ctx) {
  function Folder({ kind }) {
    const { data, isLoading, error } = useQuery({
      queryKey: [ID, 'media', kind],
      queryFn: () => ctx.rest(`/media/${kind}`),
      refetchInterval: 10000
    })
    const files = (data && data.files) || []
    return jsxs('div', {
      className: 'flex flex-col gap-0.5',
      children: [
        jsx('div', {
          className: 'text-xs font-medium text-(--ui-text-secondary)',
          children: `${kind} (${isLoading ? '…' : error ? '!' : files.length})`
        }),
        ...files.slice(0, 8).map(f =>
          jsx('div', { className: 'truncate pl-2 text-xs text-(--ui-text-tertiary)', children: f.name }, f.name)
        )
      ]
    })
  }

  return function Pane() {
    const { data, error, refetch } = useQuery({
      queryKey: [ID, 'status'],
      queryFn: () => ctx.rest('/status'),
      refetchInterval: 5000
    })
    const connected = !!(data && data.connected)
    const line = error
      ? 'backend off: enable art98 in config.yaml plugins.enabled'
      : data
        ? `${connected ? 'attached' : 'not attached'} · backend ${data.backend}`
        : 'loading…'
    return jsxs('div', {
      className: 'flex h-full flex-col gap-3 overflow-auto p-3 text-sm',
      children: [
        jsxs('div', {
          className: 'flex items-center gap-2',
          children: [
            jsx(StatusDot, { tone: connected ? 'good' : 'muted' }),
            jsx('span', { className: 'font-medium', children: 'Windows 98' }),
            jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => refetch(), children: 'Refresh' })
          ]
        }),
        jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: line }),
        ...KINDS.map(kind => jsx(Folder, { kind }, kind))
      ]
    })
  }
}

export default {
  id: ID,
  name: 'art98 (Windows 98)',
  register(ctx) {
    const Pane = makePane(ctx)
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Windows 98',
      data: { placement: 'right', width: '260px' },
      render: () => jsx(Pane, {})
    })
  }
}
