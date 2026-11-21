// SPDX-License-Identifier: Apache-2.0
use std::convert::Infallible;
use std::fmt;
use std::io;
use std::marker::PhantomData;
use std::net::Ipv4Addr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::extract::{rejection::JsonRejection, DefaultBodyLimit};
use axum::http::StatusCode;
use axum::response::{
    sse::{Event, Sse},
    IntoResponse, Response,
};
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_util::{stream, StreamExt};
use serde::de::{value::MapAccessDeserializer, MapAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{json, Value};
use tokio::net::TcpListener;

const MODEL: &str = "omlx-r-mock";
const TEXT: [&str; 2] = ["Hello", " from oMLX-R."];
const FIRST_OUTPUT_DELAY: Duration = Duration::from_millis(25);
const MAX_REQUEST_BYTES: usize = 64 * 1024;
static NEXT_ID: AtomicU64 = AtomicU64::new(1);

// Derived structs also accept positional arrays. The HTTP contract requires
// named JSON fields while keeping Serde's unknown and duplicate key checks.
struct JsonObject<T>(T);

impl<'de, T: Deserialize<'de>> Deserialize<'de> for JsonObject<T> {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct ObjectVisitor<T>(PhantomData<fn() -> T>);

        impl<'de, T: Deserialize<'de>> Visitor<'de> for ObjectVisitor<T> {
            type Value = JsonObject<T>;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a JSON object")
            }

            fn visit_map<A: MapAccess<'de>>(self, map: A) -> Result<Self::Value, A::Error> {
                T::deserialize(MapAccessDeserializer::new(map)).map(JsonObject)
            }
        }

        deserializer.deserialize_map(ObjectVisitor(PhantomData))
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ChatRequest {
    model: String,
    messages: Vec<JsonObject<Message>>,
    #[serde(default)]
    stream: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Message {
    #[serde(rename = "role")]
    _role: Role,
    #[serde(rename = "content")]
    _content: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum Role {
    System,
    User,
    Assistant,
}

struct Completion {
    id: String,
    created: u64,
}

impl Completion {
    fn new() -> Self {
        let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
        Self {
            id: format!("chatcmpl-mock-{}-{id}", std::process::id()),
            created: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs(),
        }
    }

    fn body(&self, object: &str, choice: Value) -> Value {
        json!({
            "id": self.id,
            "object": object,
            "created": self.created,
            "model": MODEL,
            "choices": [choice],
        })
    }

    fn event(&self, delta: Value, finish_reason: Option<&str>) -> Event {
        let chunk = self.body(
            "chat.completion.chunk",
            json!({"index": 0, "delta": delta, "finish_reason": finish_reason}),
        );
        Event::default().data(chunk.to_string())
    }

    fn stream(self) -> Response {
        let events = [
            (
                self.event(json!({"role": "assistant"}), None),
                Duration::ZERO,
            ),
            (
                self.event(json!({"content": TEXT[0]}), None),
                FIRST_OUTPUT_DELAY,
            ),
            (
                self.event(json!({"content": TEXT[1]}), None),
                Duration::ZERO,
            ),
            (self.event(json!({}), Some("stop")), Duration::ZERO),
            (Event::default().data("[DONE]"), Duration::ZERO),
        ];
        let stream = stream::iter(events).then(|(event, delay)| async move {
            if !delay.is_zero() {
                tokio::time::sleep(delay).await;
            }
            Ok::<_, Infallible>(event)
        });
        Sse::new(stream).into_response()
    }
}

async fn models() -> Json<Value> {
    Json(json!({
        "object": "list",
        "data": [{"id": MODEL, "object": "model", "created": 0, "owned_by": "omlx-r"}],
    }))
}

fn invalid_request(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({
            "error": {"message": message, "type": "invalid_request_error", "param": null, "code": code}
        })),
    )
        .into_response()
}

async fn chat(request: Result<Json<JsonObject<ChatRequest>>, JsonRejection>) -> Response {
    let Json(JsonObject(request)) = match request {
        Ok(request) => request,
        Err(error) => {
            let status = match error.status() {
                StatusCode::UNPROCESSABLE_ENTITY => StatusCode::BAD_REQUEST,
                status => status,
            };
            return invalid_request(status, "invalid_request", &error.body_text());
        }
    };
    if request.messages.is_empty() {
        return invalid_request(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            "messages must contain at least one message",
        );
    }
    if request.model != MODEL {
        return invalid_request(
            StatusCode::NOT_FOUND,
            "model_not_found",
            "requested model is unavailable",
        );
    }
    let completion = Completion::new();
    if request.stream {
        return completion.stream();
    }
    Json(completion.body(
        "chat.completion",
        json!({
            "index": 0,
            "message": {"role": "assistant", "content": TEXT.concat()},
            "finish_reason": "stop",
        }),
    ))
    .into_response()
}

pub fn serve(port: u16) -> io::Result<()> {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, port)).await?;
        let router = Router::new()
            .route("/v1/models", get(models))
            .route("/v1/chat/completions", post(chat))
            .layer(DefaultBodyLimit::max(MAX_REQUEST_BYTES));
        println!("Listening on http://{}", listener.local_addr()?);
        axum::serve(listener, router).await
    })
}
