import { Component, type ReactNode } from 'react'

// Contains a failing panel (e.g. Aladin's WebAssembly core) so the rest of the app keeps working.
export class ErrorBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: unknown) {
    console.error('Panel failed:', error)
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}
