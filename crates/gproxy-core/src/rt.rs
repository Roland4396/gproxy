//! The few runtime services execution needs, with one implementation per
//! target: Tokio timers and tasks natively, browser timers and the JS event
//! loop on wasm32. Nothing else in core touches a runtime directly.

use std::{future::Future, time::Duration};

/// Resolves to None when `duration` elapses first.
#[cfg(not(target_arch = "wasm32"))]
pub(crate) async fn timeout<F: Future>(duration: Duration, future: F) -> Option<F::Output> {
    if duration == std::time::Duration::MAX {
        return Some(future.await);
    }
    tokio::time::timeout(duration, future).await.ok()
}

#[cfg(target_arch = "wasm32")]
pub(crate) async fn timeout<F: Future>(duration: Duration, future: F) -> Option<F::Output> {
    if duration == std::time::Duration::MAX {
        return Some(future.await);
    }
    use futures_util::future::{Either, select};
    let future = std::pin::pin!(future);
    let timer = std::pin::pin!(sleep(duration));
    match select(future, timer).await {
        Either::Left((output, _)) => Some(output),
        Either::Right(((), _)) => None,
    }
}

#[cfg(not(target_arch = "wasm32"))]
pub(crate) async fn sleep(duration: Duration) {
    tokio::time::sleep(duration).await;
}

#[cfg(target_arch = "wasm32")]
pub(crate) async fn sleep(duration: Duration) {
    let millis = u32::try_from(duration.as_millis()).unwrap_or(u32::MAX);
    gloo_timers::future::TimeoutFuture::new(millis).await;
}

/// Run `future` to completion in the background. Natively this needs a Tokio
/// runtime and returns false without one; on wasm the JS event loop always
/// drives it.
#[cfg(not(target_arch = "wasm32"))]
pub(crate) fn spawn<F>(future: F) -> bool
where
    F: Future<Output = ()> + Send + 'static,
{
    match tokio::runtime::Handle::try_current() {
        Ok(handle) => {
            handle.spawn(future);
            true
        }
        Err(_) => false,
    }
}

#[cfg(target_arch = "wasm32")]
pub(crate) fn spawn<F>(future: F) -> bool
where
    F: Future<Output = ()> + 'static,
{
    wasm_bindgen_futures::spawn_local(future);
    true
}

#[cfg(all(test, not(target_arch = "wasm32")))]
mod no_idle_deadline_tests {
    use super::*;

    #[tokio::test(start_paused = true)]
    async fn disabled_clock_has_no_timer_and_survives_large_time_advance() {
        let (sender, receiver) = tokio::sync::oneshot::channel();
        let waiter = tokio::spawn(timeout(Duration::MAX, receiver));
        tokio::task::yield_now().await;
        tokio::time::advance(Duration::from_secs(60 * 60 * 24 * 365 * 100)).await;
        assert!(!waiter.is_finished());
        sender.send(7).unwrap();
        assert_eq!(waiter.await.unwrap(), Some(Ok(7)));
    }

    #[tokio::test(start_paused = true)]
    async fn finite_request_wait_remains_enforced() {
        let waiter = tokio::spawn(timeout(
            Duration::from_secs(1200),
            std::future::pending::<()>(),
        ));
        tokio::task::yield_now().await;
        tokio::time::advance(Duration::from_secs(1201)).await;
        assert_eq!(waiter.await.unwrap(), None);
    }

    #[tokio::test(start_paused = true)]
    async fn cancellation_still_interrupts_disabled_idle_wait() {
        let cancel = tokio_util::sync::CancellationToken::new();
        let child = cancel.clone();
        let waiter = tokio::spawn(async move {
            tokio::select! {
                () = child.cancelled() => true,
                _ = timeout(Duration::MAX, std::future::pending::<()>()) => false,
            }
        });
        tokio::task::yield_now().await;
        cancel.cancel();
        assert!(waiter.await.unwrap());
    }
}
