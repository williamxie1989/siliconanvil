// SPDX-License-Identifier: Apache-2.0
use std::convert::Infallible;
use std::future::{Future, IntoFuture};
use std::io;
use std::net::Ipv4Addr;
use std::time::Duration;

use axum::http::StatusCode;
use axum::response::{sse::Event, IntoResponse, Response, Sse};
use axum::Json;
use futures_util::{stream, StreamExt};
use serde_json::{json, Value};
use tokio::net::TcpListener;
use tokio::signal::unix::{signal, SignalKind};
use tokio::sync::{mpsc, oneshot};

use super::worker::{Admission, Client, Output, Worker};
use super::{invalid_request, router, App, ChatRequest, Completion};

pub(super) const MODEL: &str = "siliconanvil-native";
const MAX_MESSAGES: usize = 32;
const MAX_CONTENT_BYTES: usize = 4096;
const HTTP_DRAIN: Duration = Duration::from_secs(2);

fn error_body(code: &str, message: &str) -> Value {
    json!({"error": {"message": message, "type": "server_error", "param": null, "code": code}})
}

fn generation_error() -> Value {
    error_body("generation_failed", "native generation did not complete")
}

pub(super) async fn chat(worker: Client, completion: Completion, request: ChatRequest) -> Response {
    if request.messages.len() > MAX_MESSAGES
        || request
            .messages
            .iter()
            .map(|message| message.0.content.len())
            .sum::<usize>()
            > MAX_CONTENT_BYTES
    {
        return invalid_request(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "native generation accepts up to 32 messages and 4096 UTF-8 content bytes",
        );
    }
    let messages = request
        .messages
        .into_iter()
        .map(|message| message.0)
        .collect();
    let mut output = match worker.submit(messages, request.stream) {
        Ok(output) => output,
        Err(admission) => {
            let (code, message) = match admission {
                Admission::Busy => ("worker_busy", "native generation is already in progress"),
                Admission::Unavailable => ("worker_unavailable", "native worker is unavailable"),
            };
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(error_body(code, message)),
            )
                .into_response();
        }
    };
    if request.stream {
        return native_stream(completion, output);
    }
    match output.recv().await {
        Some(Output::Finished(reply)) => completion.response(reply.text, reply.finish_reason),
        _ => (StatusCode::INTERNAL_SERVER_ERROR, Json(generation_error())).into_response(),
    }
}

fn native_stream(completion: Completion, output: mpsc::Receiver<Output>) -> Response {
    let role = completion.event(json!({"role": "assistant"}), None);
    let events = stream::unfold(Some((output, completion)), |state| async {
        let (mut output, completion) = state?;
        match output.recv().await {
            Some(Output::Text(text)) => Some((
                vec![completion.event(json!({"content": text}), None)],
                Some((output, completion)),
            )),
            Some(Output::Finished(reply)) => Some((
                vec![
                    completion.event(json!({}), Some(reply.finish_reason)),
                    Event::default().data("[DONE]"),
                ],
                None,
            )),
            Some(Output::Failed) | None => Some((
                vec![Event::default().data(generation_error().to_string())],
                None,
            )),
        }
    });
    let response = stream::iter([role])
        .chain(events.flat_map(stream::iter))
        .map(Ok::<_, Infallible>);
    Sse::new(response).into_response()
}

pub(crate) fn serve_native(
    port: u16,
    start: impl FnOnce() -> Result<Worker, String>,
) -> Result<(), String> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|error| format!("start HTTP runtime: {error}"))?;
    let mut worker = None;
    let result = runtime.block_on(async {
        let mut interrupt = signal(SignalKind::interrupt()).map_err(|error| error.to_string())?;
        let mut terminate = signal(SignalKind::terminate()).map_err(|error| error.to_string())?;
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, port))
            .await
            .map_err(|error| format!("bind 127.0.0.1:{port}: {error}"))?;
        let addr = listener.local_addr().map_err(|error| error.to_string())?;
        let client = worker.insert(start()?).client();
        let router = router(App {
            native: Some(client.clone()),
        });
        let (drain, trigger) = oneshot::channel();
        let server = axum::serve(listener, router)
            .with_graceful_shutdown(async {
                let _ = trigger.await;
            })
            .into_future();
        tokio::pin!(server);
        println!("Listening on http://{addr}");
        let result = tokio::select! {
            result = &mut server => result,
            _ = interrupt.recv() => drain_http(&client, drain, server).await,
            _ = terminate.recv() => drain_http(&client, drain, server).await,
        };
        result.map_err(|error| format!("serve on {addr}: {error}"))
    });
    drop(runtime);
    let joined = worker.map(Worker::join).unwrap_or(Ok(()));
    result.and(joined)
}

async fn drain_http(
    client: &Client,
    drain: oneshot::Sender<()>,
    server: impl Future<Output = io::Result<()>>,
) -> io::Result<()> {
    client.stop();
    let _ = drain.send(());
    tokio::time::timeout(HTTP_DRAIN, server)
        .await
        .unwrap_or(Ok(()))
}
