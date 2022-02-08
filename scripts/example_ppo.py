import gym
import numpy as np
import torch
from paper_rl.common.rollout import Rollout
from paper_rl.logger.logger import Logger

from paper_rl.modelfree.ppo import PPO
from paper_rl.architecture.ac.mlp import MLPActorCritic
from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv
from stable_baselines3.common.utils import set_random_seed

from stable_baselines3.common.env_util import make_vec_env

# 2 ways to work with envs
# mpi it and have one env per process, each process has its own copy
# or use stable baselines vecenv style. - which also makes it easy to utilize the GPU.

if __name__ == "__main__":
    # env_id = "Pendulum-v0"
    env_id = "CartPole-v1"
    num_cpu = 2
    seed = 1
    env = make_vec_env(env_id, num_cpu, seed=seed)

    torch.manual_seed(seed)
    np.random.seed(seed)
    model = MLPActorCritic(env.observation_space, env.action_space, hidden_sizes=(64, 64))
    pi_optimizer = torch.optim.Adam(model.pi.parameters(), lr=1e-4)
    vf_optimizer = torch.optim.Adam(model.v.parameters(), lr=4e-4)

    # torch.set_num_threads(1)

    logger = Logger(tensorboard=True)
    steps_per_epoch = 2048 // num_cpu
    batch_size = 512
    algo = PPO(
        ac=model,
        env=env,
        num_envs=num_cpu,
        action_space=env.action_space,
        observation_space=env.observation_space,
        logger=logger,
        steps_per_epoch=steps_per_epoch,
        ent_coef=0.00,
        vf_coef=0.5,
        gamma=0.95,
        train_iters=10,  # 80 // (steps_per_epoch * num_cpu // batch_size)
    )

    def train_callback(epoch, stats):
        filtered = {}
        for k in stats.keys():
            if (
                "Epoch" in k
                or "TotalEnvInteractions" in k
                or "EpRet" in k
                or "EpLen" in k
                # or "VVals" in k
                or "LossPi_avg" in k
                or "KL_avg" in k
                or "ClipFrac_avg" in k
                or "UpdateTime_avg" in k
                or "RolloutTime_avg" in k
            ):
                filtered[k] = stats[k]
        logger.pretty_print_table(filtered)

    # algo.train(
    #     max_ep_len=1000,
    #     start_epoch=0,
    #     n_epochs=10,
    #     pi_optimizer=pi_optimizer,
    #     vf_optimizer=vf_optimizer,
    #     batch_size=batch_size,
    #     rollout_callback=None,
    #     train_callback=train_callback,
    # )
    env.close()

    eval_env = make_vec_env(env_id, 1, seed=seed)
    obs = eval_env.reset()

    for i in range(1000):
        with torch.no_grad():
            action = model.act(torch.tensor(obs), deterministic=True)
        obs, reward, done, info = eval_env.step(action)
        eval_env.render()
        if done.any():
            print(info)
    eval_env.close()

    eval_env = make_vec_env(env_id, 2, seed=seed)
    obs = eval_env.reset()
    
    rollout = Rollout()
    def policy(o):
        o = torch.as_tensor(o, dtype=torch.float32)
        return model.act(o, deterministic=True)
    expert_trajectories = rollout.collect_trajectories(policy, eval_env, n_trajectories=10, n_envs=2,)
    eval_env.close()
    for e in expert_trajectories:
        print(len(e["observations"]))
    print(f"Collected {len(expert_trajectories)} trajectories")
    np.save("weak_cartpole.npy", expert_trajectories)
