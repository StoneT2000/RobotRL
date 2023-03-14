"""
Configurations and utility classes
"""

import dataclasses
from typing import Optional

import chex
from flax import struct


@dataclasses.dataclass
class SACConfig:
    """
    Configuration dataclass for SAC
    """

    num_seed_steps: int
    """
    Number of steps to take to seed the initial replay buffer. Will generate num_seed_steps of frames of data.
    """

    replay_buffer_capacity: int
    """
    Max number of env interactions stored before the oldest entries begin to be removed. Internally it keeps track of interactions from each parallel env
    and samples them all afterwards, limiting the interactions stored per env to math.ceil(replay_buffer_capacity / num_envs).
    """

    batch_size: int
    """
    The size of the batch of rollout data sampled during gradient updates
    """

    num_envs: Optional[int] = 1
    """
    Number of parallel envs used. Each training step `step num_envs * steps_per_env` interactions are collected before updates are considered
    """

    steps_per_env: Optional[int] = 1
    """
    Usually SAC steps once through every environment before performing a gradient update. 
    You can change steps_per_env to increase the number of steps performed for each training step
    """

    grad_updates_per_step: Optional[int] = 1
    """
    Number of gradient updates for each training step.
    """

    tau: Optional[float] = 0.005
    discount: Optional[float] = 0.99
    backup_entropy: Optional[bool] = True
    target_entropy: Optional[float] = None
    """
    This defaults to `-act_dims / 2`
    """
    learnable_temp: Optional[bool] = True
    """
    If true, the alpha/temperature is learnable and changes, minimizing `temperature * (entropy - target_entropy).mean()` where `entropy`
    is the mean negative log probability of the actor policy taking a batch of actions for a given batch of observations
    """
    initial_temperature: Optional[float] = 1.0
    """
    The initial alphaa/temperature to use
    """
    actor_update_freq: Optional[int] = 1
    """
    Frequency at which to update the actor policy
    """
    target_update_freq: Optional[int] = 1
    """
    Frequency at which to update the target network
    """

    eval_freq: Optional[int] = 5000
    """
    Every eval_freq interactions an evaluation is performed
    """
    eval_steps: Optional[int] = 1000
    """
    Number of evaluation steps taken for each eval environment
    """
    num_eval_envs: Optional[int] = 4
    """
    Number of evaluation envs to use
    """

    log_freq: Optional[int] = 1000
    """
    Every log_freq interactions metrics (e.g. critic loss) are logged
    """
    save_freq: Optional[int] = 100_000
    """
    Every save_freq interactions the current training state is saved.
    """


@struct.dataclass
class TimeStep:
    action: chex.Array = None
    env_obs: chex.Array = None
    next_env_obs: chex.Array = None
    reward: chex.Array = None
    mask: chex.Array = None
