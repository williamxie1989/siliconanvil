// SPDX-License-Identifier: Apache-2.0
use std::sync::{mpsc as mailbox, Arc};
use std::thread::{self, JoinHandle};

use tokio::sync::{mpsc, OwnedSemaphorePermit, Semaphore, TryAcquireError};

use super::Message;

#[cfg(test)]
mod tests;

pub(crate) const MAX_TOKENS: usize = 32;

pub(crate) struct Reply {
    pub text: String,
    pub finish_reason: &'static str,
}

pub(super) enum Output {
    Text(String),
    Finished(Reply),
    Failed,
}

#[derive(Debug)]
pub(super) enum Admission {
    Busy,
    Unavailable,
}

pub(crate) trait Engine {
    fn generate(&mut self, messages: Vec<Message>, output: &JobOutput) -> Result<Reply, String>;
    fn shutdown(self) -> Result<(), String>;
}

pub(crate) struct JobOutput {
    sender: mpsc::Sender<Output>,
    admission: Arc<Semaphore>,
    pub stream: bool,
}

impl JobOutput {
    pub(crate) fn is_cancelled(&self) -> bool {
        self.admission.is_closed() || self.sender.is_closed()
    }

    pub(crate) fn text(&self, text: String) -> bool {
        !self.is_cancelled() && self.sender.try_send(Output::Text(text)).is_ok()
    }
}

struct Job {
    messages: Vec<Message>,
    output: JobOutput,
    permit: OwnedSemaphorePermit,
}

enum Command {
    Generate(Job),
    Stop,
}

#[derive(Clone)]
pub(super) struct Client {
    commands: mailbox::SyncSender<Command>,
    admission: Arc<Semaphore>,
}

impl Client {
    pub(super) fn submit(
        &self,
        messages: Vec<Message>,
        stream: bool,
    ) -> Result<mpsc::Receiver<Output>, Admission> {
        let permit = self
            .admission
            .clone()
            .try_acquire_owned()
            .map_err(|error| match error {
                TryAcquireError::Closed => Admission::Unavailable,
                TryAcquireError::NoPermits => Admission::Busy,
            })?;
        // One delta per token, one final detokenization delta, and a terminal result.
        let (sender, receiver) = mpsc::channel(MAX_TOKENS + 2);
        self.commands
            .try_send(Command::Generate(Job {
                messages,
                output: JobOutput {
                    sender,
                    admission: self.admission.clone(),
                    stream,
                },
                permit,
            }))
            .map_err(|_| Admission::Unavailable)?;
        Ok(receiver)
    }

    pub(super) fn stop(&self) {
        self.admission.close();
        // If a job occupies the mailbox, it will wake the worker and observe closure.
        let _ = self.commands.try_send(Command::Stop);
    }
}

pub(crate) struct Worker {
    client: Client,
    thread: JoinHandle<Result<(), String>>,
}

impl Worker {
    pub(crate) fn start<E: Engine>(
        load: impl FnOnce() -> Result<E, String> + Send + 'static,
    ) -> Result<Self, String> {
        let admission = Arc::new(Semaphore::new(1));
        let (commands, incoming) = mailbox::sync_channel(1);
        let (ready, startup) = mailbox::sync_channel(0);
        let thread = thread::Builder::new()
            .name("siliconanvil-inference".into())
            .spawn(move || {
                let mut engine = load()?;
                if ready.send(()).is_ok() {
                    while let Ok(Command::Generate(job)) = incoming.recv() {
                        if job.output.admission.is_closed() {
                            break;
                        }
                        if job.output.is_cancelled() {
                            continue;
                        }
                        let reply = engine.generate(job.messages, &job.output);
                        drop(job.permit);
                        if !job.output.is_cancelled() {
                            let terminal = match reply {
                                Ok(reply) => Output::Finished(reply),
                                Err(error) => {
                                    eprintln!("siliconanvil inference: {error}");
                                    Output::Failed
                                }
                            };
                            let _ = job.output.sender.try_send(terminal);
                        }
                    }
                }
                engine.shutdown()
            })
            .map_err(|error| format!("start inference worker: {error}"))?;
        let worker = Self {
            client: Client {
                commands,
                admission,
            },
            thread,
        };
        if startup.recv().is_err() {
            worker.join()?;
            return Err("inference worker exited before becoming ready".into());
        }
        Ok(worker)
    }

    pub(super) fn client(&self) -> Client {
        self.client.clone()
    }

    pub(super) fn join(self) -> Result<(), String> {
        self.client.stop();
        self.thread
            .join()
            .map_err(|_| "inference worker panicked")?
    }
}
