// SPDX-License-Identifier: Apache-2.0
use super::*;
use std::rc::Rc;
use std::time::Duration;

struct ScriptedEngine {
    owner: Rc<thread::ThreadId>,
    cancelled: std::sync::mpsc::Sender<()>,
}

impl Engine for ScriptedEngine {
    fn generate(&mut self, messages: Vec<Message>, output: &JobOutput) -> Result<Reply, String> {
        assert_eq!(*self.owner, thread::current().id());
        assert!(matches!(messages[0].role, super::super::Role::User));
        if messages[0].content == "hold" {
            assert!(output.stream);
            assert!(output.text("started".into()));
            let deadline = std::time::Instant::now() + Duration::from_secs(3);
            while !output.is_cancelled() {
                assert!(std::time::Instant::now() < deadline);
                thread::sleep(Duration::from_millis(1));
            }
            self.cancelled.send(()).unwrap();
        }
        Ok(Reply {
            text: "completed".into(),
            finish_reason: "stop",
        })
    }

    fn shutdown(self) -> Result<(), String> {
        assert_eq!(*self.owner, thread::current().id());
        Ok(())
    }
}

#[test]
fn admission_recovers_after_disconnect_and_shutdown_joins_the_owner() {
    let (cancelled, observed) = std::sync::mpsc::channel();
    let worker = Worker::start(move || {
        Ok(ScriptedEngine {
            owner: Rc::new(thread::current().id()),
            cancelled,
        })
    })
    .unwrap();
    let client = worker.client();
    let submit = |content: &str, stream| {
        client.submit(
            vec![Message {
                role: super::super::Role::User,
                content: content.into(),
            }],
            stream,
        )
    };
    let mut first = submit("hold", true).unwrap();
    assert!(matches!(first.blocking_recv(), Some(Output::Text(text)) if text == "started"));
    assert!(matches!(submit("second", false), Err(Admission::Busy)));
    drop(first);
    observed.recv_timeout(Duration::from_secs(3)).unwrap();

    let deadline = std::time::Instant::now() + Duration::from_secs(3);
    let mut active = loop {
        match submit("hold", true) {
            Ok(active) => break active,
            Err(Admission::Busy) if std::time::Instant::now() < deadline => {
                thread::sleep(Duration::from_millis(1));
            }
            _ => panic!("worker did not recover after disconnect"),
        }
    };
    assert!(matches!(active.blocking_recv(), Some(Output::Text(_))));
    client.stop();
    assert!(matches!(submit("late", false), Err(Admission::Unavailable)));
    worker.join().unwrap();
    observed.recv_timeout(Duration::from_secs(3)).unwrap();
    assert!(active.blocking_recv().is_none());
}

#[test]
fn non_streaming_job_returns_its_terminal_result() {
    let (cancelled, _) = std::sync::mpsc::channel();
    let worker = Worker::start(move || {
        Ok(ScriptedEngine {
            owner: Rc::new(thread::current().id()),
            cancelled,
        })
    })
    .unwrap();
    let mut result = worker
        .client()
        .submit(
            vec![Message {
                role: super::super::Role::User,
                content: "finish".into(),
            }],
            false,
        )
        .unwrap();
    let Some(Output::Finished(reply)) = result.blocking_recv() else {
        panic!("expected a completed reply");
    };
    assert_eq!(reply.text, "completed");
    assert_eq!(reply.finish_reason, "stop");
    worker.join().unwrap();
}
