import multiprocessing
import os
import itertools
import importlib
import time

import cache as cachelib

import numpy as np
import random
from multiprocessing import Pool, cpu_count
from io import StringIO
import statistics
import argparse
import sys
import json

parser = argparse.ArgumentParser(description="Run the Prisoner's Dilemma simulation.")
parser.add_argument(
    "-n",
    "--num-runs",
    dest="num_runs",
    type=int,
    default=100,
    help="Number of runs to average out",
)

parser.add_argument(
    "-d",
    "--det-turns",
    dest="deterministic_turns",
    type=int,
    default=500,
    help="Number of turns in a deterministic run",
)

parser.add_argument(
    "--skip-slow",
    dest="use_slow",
    action="store_false",
    help="Skip slow strategies for better performance",
)

parser.add_argument(
    "-s",
    "--strategies",
    dest="strategies",
    nargs="+",
    help="If passed, only these strategies will be tested against each other. If only a single strategy is passed, every other strategy will be paired against it.",
)

cacheparser = parser.add_argument_group("Cache")

cacheparser.add_argument(
    "--no-cache",
    dest="cache",
    action="store_false",
    default=True,
    help="Ignores the cache."
)

cacheparser.add_argument(
    "--delete-cache",
    "--remove-cache",
    dest="delete_cache",
    action="store_true",
    default=False,
    help="Deletes the cache."
)

cacheparser.add_argument(
    "-k",
    "--cache-backend",
    dest="cache_backend",
    type=str,
    default="sqlite",
    help="Specifies which cache backend to use. (sqlite or json)"
)

cacheparser.add_argument(
    "--cache-file",
    dest="cache_file",
    type=str,
    default="",
    help="Specifies the cache file to use."
)

parser.add_argument(
    "--no-weights",
    dest="weights",
    action="store_false",
    default=True,
    help="Ignores weights set in weights.json."
)

parser.add_argument(
    "-j",
    "--num-processes",
    dest="processes",
    type=int,
    default=cpu_count(),
    help="Number of processes to run the simulation with. By default, this is the same as your CPU core count.",
)


args = parser.parse_args()

DETERMINISTIC_TURNS = args.deterministic_turns

if DETERMINISTIC_TURNS < 200:
    raise Exception("--det-turns must be at least 200")

STRATEGY_FOLDERS = [p for p in os.listdir() if os.path.isdir(p)]
if not args.use_slow:
    STRATEGY_FOLDERS.remove("slow")
RESULTS_FILE = "results.txt"
RESULTS_HTML = "results.html"
RESULTS_JSON = "results.json"
SUMMARY_FILE = "summary.txt"
PROFILE_FILE = "profile.txt"
NUM_RUNS = args.num_runs

pointsArray = [
    [1, 5],
    [0, 3],
]  # The i-j-th element of this array is how many points you receive if you do play i, and your opponent does play j.
moveLabels = ["D", "C"]
# D = defect,     betray,       sabotage,  free-ride,     etc.
# C = cooperate,  stay silent,  comply,    upload files,  etc.

def strategyMove(move):
    if type(move) is str:
        defects = ["defect", "tell truth"]
        return 0 if (move in defects) else 1
    else:
        return move


def runRound(moduleA, moduleB):
    memoryA = None
    memoryB = None

    # The games are a minimum of 200 turns long.
    # The np.log here guarantees that every turn after the 200th has an equal (low) chance of being the final turn.
    LENGTH_OF_GAME = int(
        200 - 40 * np.log(1-random.random())
    )
    history = np.zeros((2, LENGTH_OF_GAME), dtype=int)
    historyFlipped = np.zeros((2,LENGTH_OF_GAME),dtype=int)

    for turn in range(LENGTH_OF_GAME):
        # Copy history so that players cannot rewrite it
        playerAmove, memoryA = moduleA.strategy(history[:,:turn].copy(),memoryA)
        playerBmove, memoryB = moduleB.strategy(historyFlipped[:,:turn].copy(),memoryB)
        history[0, turn] = strategyMove(playerAmove)
        history[1, turn] = strategyMove(playerBmove)
        historyFlipped[0,turn] = history[1,turn]
        historyFlipped[1,turn] = history[0,turn]

    return history

turnChances = []

def turnChance(x,summing=False):
    if x == 0:
        return 1/40
    if summing:
        S = turnChance(x-1,True)
        return (1-S)/40+S
    return (1-turnChance(x-1,True))/40

for i in range(DETERMINISTIC_TURNS-199):
    turnChances.append(turnChance(i))

# this is so that deterministic algorithms still get 3 points for always Coop,
# instead of 2.999
chancesSum = sum(turnChances)
turnChances = [i/chancesSum for i in turnChances]

def runDeterministic(moduleA, moduleB):
    memoryA = None
    memoryB = None
    memoryA2 = None
    memoryB2 = None

    history = np.zeros((2,DETERMINISTIC_TURNS),dtype=int)
    historyFlipped = np.zeros((2,DETERMINISTIC_TURNS),dtype=int)

    for turn in range(DETERMINISTIC_TURNS):
        playerAmove, memoryA = moduleA.strategy(history[:,:turn].copy(),memoryA)
        playerBmove, memoryB = moduleB.strategy(historyFlipped[:,:turn].copy(),memoryB)
        history[0, turn] = strategyMove(playerAmove)
        history[1, turn] = strategyMove(playerBmove)

        playerAmove2, memoryA2 = moduleA.strategy(history[:,:turn].copy(),memoryA2)
        playerBmove2, memoryB2 = moduleB.strategy(historyFlipped[:,:turn].copy(),memoryB2)

        if strategyMove(playerAmove2) != strategyMove(playerAmove):
            return False
        if strategyMove(playerBmove2) != strategyMove(playerBmove):
            return False

        historyFlipped[0,turn] = history[1,turn]
        historyFlipped[1,turn] = history[0,turn]

    totals = [0,0]
    scores = [0,0]

    for turn in range(199):
        scores[0] += pointsArray[history[0,turn]][history[1,turn]]
        scores[1] += pointsArray[history[1,turn]][history[0,turn]]

    for turn in range(199,DETERMINISTIC_TURNS):
        scores[0] += pointsArray[history[0,turn]][history[1,turn]]
        scores[1] += pointsArray[history[1,turn]][history[0,turn]]

        totals[0] += scores[0]/(turn+1)*turnChances[turn-199]
        totals[1] += scores[1]/(turn+1)*turnChances[turn-199]

    return totals, history


def tallyRoundScores(history):
    scoreA = 0
    scoreB = 0
    roundLength = history.shape[1]
    for turn in range(roundLength):
        playerAmove = history[0, turn]
        playerBmove = history[1, turn]
        scoreA += pointsArray[playerAmove][playerBmove]
        scoreB += pointsArray[playerBmove][playerAmove]
    return scoreA / roundLength, scoreB / roundLength


def outputRoundResults(f, pair, roundHistory, scoresA, scoresB, stdevA, stdevB):
    f.write(f"{pair[0]} (P1)  VS.  {pair[1]} (P2)\n")
    for p in range(2):
        for t in range(roundHistory.shape[1]):
            move = roundHistory[p, t]
            f.write(moveLabels[move] + " ")
        f.write("\n")
    f.write(f"Final score for {pair[0]}: {scoresA} ± {stdevA}\n")
    f.write(f"Final score for {pair[1]}: {scoresB} ± {stdevB}\n")
    f.write("\n")


def pad(stri, leng):
    result = stri
    for i in range(len(stri), leng):
        result = result + " "
    return result


def progressBar(width, completion):
    numCompleted = round(width * completion)
    return f"[{'=' * numCompleted}{' ' * (width - numCompleted)}]"


def runRounds(pair):
    # If round results are cached, return the cached results instead
    if args.cache:
        cache = cachelib.get_backend(args, lock=lock)
        r = cache.get(pair)
        if r:
            cache.close()
            return True, *r, 0

    startTime = time.time()

    allScoresA = []
    allScoresB = []
    firstRoundHistory = None

    moduleA = importlib.import_module(pair[0])
    moduleB = importlib.import_module(pair[1])

    deterministic = runDeterministic(moduleA, moduleB)

    if deterministic:
        allScoresA = [deterministic[0][0]]
        allScoresB = [deterministic[0][1]]
        firstRoundHistory = deterministic[1]
    else:
        for i in range(NUM_RUNS):
            roundHistory = runRound(moduleA, moduleB)
            scoresA, scoresB = tallyRoundScores(roundHistory)
            if i == 0:
                firstRoundHistory = roundHistory
            allScoresA.append(scoresA)
            allScoresB.append(scoresB)

    avgScoreA = statistics.mean(allScoresA)
    avgScoreB = statistics.mean(allScoresB)

    endTime = time.time()

    # Standard deviation throws an error with <2 data points (run with -n1).
    # In that case, set it to 0 instead.
    stdevA = statistics.stdev(allScoresA) if len(allScoresA) > 1 else 0
    stdevB = statistics.stdev(allScoresB) if len(allScoresB) > 1 else 0

    roundResults = StringIO()
    outputRoundResults(
        roundResults, pair, firstRoundHistory, avgScoreA, avgScoreB, stdevA, stdevB
    )
    roundResults.flush()
    roundResultsStr = roundResults.getvalue()
    roundResults.close()

    if args.cache:
        cache.insert(pair, avgScoreA, avgScoreB, stdevA, stdevB, firstRoundHistory, roundResultsStr)
        cache.close()

    return False, avgScoreA, avgScoreB, stdevA, stdevB, firstRoundHistory, roundResultsStr, endTime - startTime


def pool_init(l):
    global lock
    lock = l


def runFullPairingTournament(inFolders, outFile, summaryFile):
    startTime = time.time()
    print("Starting tournament, reading files from " + ", ".join(inFolders))
    if args.delete_cache:
        try:
            cache = cachelib.get_backend(args)
            file = args.cache_file
            os.remove(file if file != "" else cache.default)
        except FileNotFoundError:
            pass

    if args.cache:
        cache = cachelib.get_backend(args)
        cache.setup()

    scoreKeeper = {}
    STRATEGY_LIST = []
    for inFolder in inFolders:
        for file in os.listdir(inFolder):
            if file.endswith(".py"):
                STRATEGY_LIST.append(f"{inFolder}.{file[:-3]}")

    if args.strategies is not None and len(args.strategies) > 1:
        STRATEGY_LIST = [strategy for strategy in STRATEGY_LIST if strategy in args.strategies]

    if len(STRATEGY_LIST) < 2:
        raise ValueError('Not enough strategies!')

    for strategy in STRATEGY_LIST:
        scoreKeeper[strategy] = 0

    mainFile = open(outFile, "w+")
    summaryFile = open(summaryFile, "w+")

    combinations = list(itertools.combinations(STRATEGY_LIST, r=2))

    if args.strategies is not None and len(args.strategies) == 1:
        combinations = [pair for pair in combinations if pair[0] == args.strategies[0] or pair[1] == args.strategies[0]]

    numCombinations = len(combinations)
    allResults = []
    strategyTimes = dict((k, 0) for k in STRATEGY_LIST)
    with Pool(args.processes, initializer=pool_init, initargs=(multiprocessing.Lock(),)) as p:
        hits = 0
        for i, result in enumerate(
            zip(p.imap(runRounds, combinations), combinations), 1
        ):
            (
                cached,
                avgScoreA,
                avgScoreB,
                stdevA,
                stdevB,
                firstRoundHistory,
                roundResultsStr,
                pairTime
            ) = result[0]

            if cached:
                hits += 1

            sys.stdout.write(
                f"\r{i}/{numCombinations} pairings ({NUM_RUNS} runs per pairing, {hits} hits, {i-hits} misses) {progressBar(50, i / numCombinations)}"
            )
            sys.stdout.flush()
            (nameA, nameB) = result[1]
            scoresList = [avgScoreA, avgScoreB]

            strategyTimes[nameA] += pairTime
            strategyTimes[nameB] += pairTime

            allResults.append(
                {
                    "playerA": {
                        "name": nameA,
                        "avgScore": avgScoreA,
                        "stdev": stdevA,
                        "history": firstRoundHistory[0].tolist()
                    },
                    "playerB": {
                        "name": nameB,
                        "avgScore": avgScoreB,
                        "stdev": stdevB,
                        "history": firstRoundHistory[1].tolist()
                    }
                }
            )
            mainFile.write(roundResultsStr)
            scoreKeeper[nameA] += avgScoreA
            scoreKeeper[nameB] += avgScoreB
    sys.stdout.write("\n")
    sys.stdout.flush()

    with open(RESULTS_JSON, "w+") as j:
        j.write(json.dumps(allResults))

    scoresNumpy = np.zeros(len(scoreKeeper))
    for i in range(len(STRATEGY_LIST)):
        scoresNumpy[i] = scoreKeeper[STRATEGY_LIST[i]]
    rankings = np.argsort(scoresNumpy)
    invRankings = [len(rankings) - int(ranking) - 1 for ranking in np.argsort(rankings)]

    with open("viewer-template.html", "r+") as t:
        jsonStrategies = [
            {
                "name": name,
                "rank": rank,
                "score": score,
                "avgScore": score / (len(STRATEGY_LIST) - 1),
                "time": time
            }
            for (name, rank, score, time) in zip(STRATEGY_LIST, invRankings, scoresNumpy, (strategyTimes[k] for k in STRATEGY_LIST))
        ]
        jsonResults = json.dumps({"results": allResults, "strategies": jsonStrategies})
        templateStr = t.read()
        with open(RESULTS_HTML, "w+") as out:
            out.write(templateStr.replace("$results", jsonResults))

    mainFile.write("\n\nTOTAL SCORES\n")
    for rank in range(len(STRATEGY_LIST)):
        i = rankings[-1 - rank]
        score = scoresNumpy[i]
        scorePer = score / (len(STRATEGY_LIST) - 1)
        scoreLine = f"#{rank + 1}: {pad(STRATEGY_LIST[i] + ':', 16)}{score:.3f}  ({scorePer:.3f} average)\n"
        mainFile.write(scoreLine)
        summaryFile.write(scoreLine)

    with open(PROFILE_FILE, "w+") as profileFile:
        strategyTimesSorted = sorted(strategyTimes.items(), key=lambda x: x[1], reverse=True)
        for strategy, stratTime in strategyTimesSorted:
            profileFile.write(f"{strategy}: {stratTime:.3f} sec\n")

    mainFile.flush()
    mainFile.close()
    summaryFile.flush()
    summaryFile.close()
    print(f"Done with everything! ({time.time() - startTime}) Results file written to {RESULTS_FILE}")


if __name__ == "__main__":
    runFullPairingTournament(STRATEGY_FOLDERS, RESULTS_FILE, SUMMARY_FILE)
