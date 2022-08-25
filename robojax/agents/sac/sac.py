import time
from dis import disco
from functools import partial
from typing import Any, Callable, Tuple

import distrax
import jax
import jax.numpy as jnp
import numpy as np
from chex import Array, PRNGKey
from flax import struct

from robojax.agents.base import BasePolicy
from robojax.agents.sac.config import SACConfig, TimeStep
from robojax.agents.sac.networks import ActorCritic, DiagGaussianActor
from robojax.data import buffer
from robojax.data.buffer import GenericBuffer
from robojax.data.loop import EnvAction, EnvObs, GymLoop, JaxLoop
from robojax.logger.logger import Logger
from robojax.models import Model
from robojax.models.model import Params
from robojax.agents.sac import loss

class SAC(BasePolicy):
    def __init__(
        self,
        jax_env: bool,
        observation_space,
        action_space,
        seed_sampler: Callable[[PRNGKey], EnvAction] = None,
        env=None,
        eval_env=None,
        cfg: SACConfig = {},
    ):
        super().__init__(jax_env, env)
        if isinstance(cfg, dict):
            self.cfg = SACConfig(**cfg)
        else:
            self.cfg = cfg

        self.step = 0
        if seed_sampler is None:
            seed_sampler = lambda rng_key: self.env.action_space().sample(rng_key)
            # TODO add a nice error message if this guessed sampler doesn't work
        self.seed_sampler = seed_sampler

        buffer_config = dict(
            action=((self.action_dim,), action_space.dtype),
            reward=((), np.float32),
            mask=((), float),
        )
        if isinstance(self.obs_shape, dict):
            buffer_config["env_obs"] = (
                self.obs_shape,
                {k: self.observation_space[k].dtype for k in self.observation_space},
            )
        else:
            buffer_config["env_obs"] = (self.obs_shape, np.float32)
        buffer_config["next_env_obs"] = buffer_config["env_obs"]
        self.replay_buffer = GenericBuffer(
            buffer_size=self.cfg.replay_buffer_capacity, n_envs=1, config=buffer_config
        )

        if self.cfg.target_entropy is None:
            self.cfg.target_entropy = -self.action_dim / 2

        if self.jax_env:
            self._env_step = jax.jit(self._env_step, static_argnames=["seed"])
            self.eval_loop = JaxLoop(
                eval_env.reset,
                eval_env.step,
                reset_env=True,
            )
        else:
            self.eval_loop = GymLoop(eval_env)

    @partial(jax.jit, static_argnames=["self", "seed"])
    def _sample_action(self, rng_key, actor: DiagGaussianActor, env_obs, seed=False):
        if seed:
            a = self.seed_sampler(rng_key)
        else:
            dist: distrax.Distribution = actor(env_obs)
            a = dist.sample(seed=rng_key)
        return a

    def _env_step(self, rng_key: PRNGKey, env_obs, env_state, actor: DiagGaussianActor, seed=False):
        rng_key, act_rng_key, env_rng_key = jax.random.split(rng_key, 3)
        a = self._sample_action(act_rng_key, actor, env_obs, seed)
        if self.jax_env:
            next_env_obs, next_env_state, reward, done, info = self.env_step(
                env_rng_key, env_state, a
            )
        else:
            a = np.asarray(a)
            next_env_obs, reward, done, info = self.env.step(a)
            next_env_state = None
        return a, next_env_obs, next_env_state, reward, done, info

    def train(self, rng_key: PRNGKey, ac: ActorCritic, logger: Logger, verbose=1):
        stime = time.time()
        episodes = 0
        ep_lens, ep_rets, dones = np.zeros(self.cfg.num_envs), np.zeros(self.cfg.num_envs), np.zeros(self.cfg.num_envs)
        rng_key, reset_rng_key = jax.random.split(rng_key, 2)
        if self.jax_env:
            env_obs, env_state = self.env_reset(reset_rng_key)
        else:
            env_obs = self.env.reset()
            env_states = None
        from tqdm import tqdm
        pbar=tqdm(total=self.cfg.num_train_steps)
        while self.step < self.cfg.num_train_steps:
            if self.step % self.cfg.eval_freq == 0 and self.step > 0 and self.step >= self.cfg.num_seed_steps and self.cfg.eval_freq > 0:
                rng_key, eval_rng_key = jax.random.split(rng_key, 2)
                self.evaluate(eval_rng_key, ac, logger)
            if dones.any():
                logger.store(
                    tag="train",
                    ep_ret=ep_rets[dones],
                    ep_len=ep_lens[dones],
                    append=False
                )
                stats = logger.log(self.step)
                logger.reset()
                episodes += dones.sum()
                ep_lens[dones] = 0.0
                ep_rets[dones] = 0.0
            
            rng_key, env_rng_key = jax.random.split(rng_key, 2)
        
            actions, next_env_obs, next_env_states, rewards, dones, infos = self._env_step(env_rng_key, env_obs, env_states, ac.actor, seed=self.step < self.cfg.num_seed_steps)
            dones = np.array(dones)
            rewards = np.array(rewards)

            ep_lens += 1
            ep_rets += rewards
            self.step += 1

            mask = (~dones) | (ep_lens == self.cfg.max_episode_length)
            # if not done or 'TimeLimit.truncated' in info:
            #     mask = 1.0
            # else:
            #     # 0 here means we don't use the q value of the next state and action.
            #     # we bootstrap whenever we have a time limit termination
            #     mask = 0.0
            self.replay_buffer.store(
                env_obs=env_obs,
                reward=rewards,
                action=actions,
                mask=mask,
                next_env_obs=next_env_obs,
            )

            env_obs = next_env_obs
            env_states = next_env_states

            # update policy
            if self.step >= self.cfg.num_seed_steps:
                rng_key, update_rng_key, sample_key = jax.random.split(rng_key, 3)
                update_actor = self.step % self.cfg.actor_update_freq == 0
                update_target = self.step % self.cfg.target_update_freq == 0
                batch = self.replay_buffer.sample_random_batch(
                    sample_key, self.cfg.batch_size
                )
                batch = TimeStep(**batch)
                (
                    new_actor,
                    new_critic,
                    new_target_critic,
                    new_temp,
                    aux,
                ) = self.update_parameters(
                    update_rng_key,
                    ac.actor,
                    ac.critic,
                    ac.target_critic,
                    ac.temp,
                    batch,
                    update_actor,
                    update_target,
                )
                ac.actor = new_actor
                ac.critic = new_critic
                ac.target_critic = new_target_critic
                ac.temp = new_temp
                critic_update_aux: loss.CriticUpdateAux = aux["critic_update_aux"]
                actor_update_aux: loss.ActorUpdateAux = aux["actor_update_aux"]
                temp_update_aux: loss.TempUpdateAux = aux["temp_update_aux"]
                if self.cfg.log_freq > 0 and self.step % self.cfg.log_freq == 0:
                    logger.store(
                        tag="train",
                        append=False,
                        critic_loss=float(critic_update_aux.critic_loss),
                        q1=float(critic_update_aux.q1),
                        q2=float(critic_update_aux.q2),
                        temp=float(temp_update_aux.temp),
                    )
                    if update_actor:
                        logger.store(
                            tag="train",
                            actor_loss=float(actor_update_aux.actor_loss),
                            entropy=float(actor_update_aux.entropy),
                            target_entropy=float(self.cfg.target_entropy),
                            append=False
                        )
                        if self.cfg.learnable_temp:
                            logger.store(tag="train", temp_loss=float(temp_update_aux.temp_loss), append=False)
                    stats = logger.log(self.step)
                    logger.reset()
            pbar.update(n=1)
            
    @partial(jax.jit, static_argnames=["self", "update_actor", "update_target"])
    def update_parameters(
        self,
        rng_key: PRNGKey,
        actor: Model,
        critic: Model,
        target_critic: Model,
        temp: Model,
        batch: TimeStep,
        update_actor: bool,
        update_target: bool,
    ):
        rng_key, critic_update_rng_key = jax.random.split(rng_key, 2)
        new_critic, critic_update_aux = loss.update_critic(critic_update_rng_key, actor, critic, target_critic, temp, batch, self.cfg.discount, self.cfg.backup_entropy)
        new_actor, actor_update_aux = actor, loss.ActorUpdateAux()
        new_temp, temp_update_aux = temp, loss.TempUpdateAux(temp=temp())
        new_target = target_critic
        if update_target:
            new_target = loss.update_target(critic, target_critic, self.cfg.tau)
        if update_actor:
            rng_key, actor_update_rng_key = jax.random.split(rng_key, 2)
            new_actor, actor_update_aux = loss.update_actor(actor_update_rng_key, actor, critic, temp, batch)
            if self.cfg.learnable_temp:
                new_temp, temp_update_aux = loss.update_temp(temp, actor_update_aux.entropy, self.cfg.target_entropy)
        return (
            new_actor,
            new_critic,
            new_target,
            new_temp,
            dict(
                critic_update_aux=critic_update_aux,
                actor_update_aux=actor_update_aux,
                temp_update_aux=temp_update_aux,
            ),
        )
    def evaluate(self, rng_key: PRNGKey, ac: ActorCritic, logger: Logger):
        rng_key, *eval_rng_keys = jax.random.split(rng_key, self.cfg.num_eval_envs + 1)      
        eval_buffer, _ = self.eval_loop.rollout(rng_keys=jnp.stack(eval_rng_keys),
            params=ac.actor,
            apply_fn=ac.act,
            steps_per_env=self.cfg.eval_steps,
        )
        eval_ep_lens = np.asarray(eval_buffer['ep_len'])
        eval_ep_rets = np.asarray(eval_buffer['ep_ret'])
        eval_episode_ends = np.asarray(eval_buffer['done'])
        eval_ep_rets = eval_ep_rets[eval_episode_ends].flatten()
        eval_ep_lens = eval_ep_lens[eval_episode_ends].flatten()
        logger.store(
            tag="test",
            ep_ret=eval_ep_rets,
            ep_len=eval_ep_lens,
            append=False,
        )
        logger.log(self.step)
        logger.reset()