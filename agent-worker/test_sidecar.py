"""The whole suite, in one importable place.

The tests themselves live in tests/, one module per subject — this file is the
aggregator so that `python -m unittest test_sidecar` remains the single command
CI, the pre-commit hook, the wavetalk-test skill and CLAUDE.md all name.

Run from agent-worker/:  python -m unittest test_sidecar -v

Deliberately stdlib-only (unittest, tempfile) so the venv needs nothing new.
Network is never touched.
"""

from __future__ import annotations

# Sets LOG_TO_FILE before any module that logs is imported. Must come first.
import tests  # noqa: F401

from tests.test_settings import (  # noqa: F401
    TestAConfigValueCannotNameAFileOnTheDisk,
    TestANeighbouringServiceIsNotOnLocalhost,
    TestNoSettingIsSmuggledThroughTheEnvironment,
    TestOneSettingReplacingAnotherSaysSo,
    TestSettings,
    TestEverySecretRendersSomewhere,
    TestSettingsThatAreOnlyWrongTogether,
    TestTheDataDirCheckCannotStopTheWorker,
    TestTheProviderTablesAgreeWithEachOther,
    TestTheModelWarmedIsTheModelUsed,
    TestTurnTakingDelaysAreOptOut,
    TestTurnTakingSettingsReachTheCall,
    TestUploadedSoundsCannotFillTheVolume,
    TestTheGuestExpiryMovedToHoursWithoutMovingAnyonesExpiry,
)
from tests.test_secrets_and_auth import (  # noqa: F401
    TestAdminAuth,
    TestAnUnreadablePasswordStoreFailsClosed,
    TestFirstRunIsNotOpenToTheWeb,
    TestFrontDoorPolicy,
    TestSecrets,
    TestStoredKeysStayHome,
    TestWrittenFilesGetExplicitModes,
)
from tests.test_http import (  # noqa: F401
    TestAMissingModelNamesTheOnesTheServerHas,
    TestAnUnsignedWebhookCannotFillMemory,
    TestCallerIdentityCannotBeChosen,
    TestCallerIdentitySurvivesTwoProxies,
    TestHttpSurface,
    TestJoinTokensExpire,
    TestTheModelListFollowsTheEndpoint,
    TestUsageControls,
)
from tests.test_widget import (  # noqa: F401
    TestAHostThemeIsADefaultNotADecree,
    TestAssetVersioning,
    TestEachSurfaceIsAnsweredDeliberately,
    TestPanelLoadsOnOpen,
    TestPanelMarkup,
    TestPushToTalkIsPerSurfaceAndOnByDefault,
    TestSoundPacks,
    TestTheCallButtonSaysWhatTheOperatorChose,
    TestTheCallButtonSurvivesTheUpgrade,
    TestTheCallerCanChooseWhichWayOut,
    TestTheCardIsOneHeightAndStaysThere,
    TestThePreviewCannotDisagreeWithTheCard,
    TestTheServiceWorkerStaysOutOfTheWay,
    TestTheStationsOwnColoursReachTheCard,
    TestTheWidgetActuallyParses,
    TestTheStatusChipDescribesTheCallNotTheSDK,
    TestWidgetServerContract,
    TestThePaletteTravelsForTheCycle,
    TestAVoicemailOnlyLineHasOneDoor,
    TestTheEmbedIsJustTheCard,
    TestTheKillSwitchOutranksEveryDoor,
    TestTheLauncherIsAPhoneInThePocket,
    TestTheEffectHasADial,
    TestThePanelReadsAtAGlance,
    TestTheBeepIsPreviewableAndWavOnly,
    TestHiddenActuallyHides,
    TestTheStylesheetParsesToTheEnd,
    TestTheUrlRowsOnlyExistInUrlMode,
)
from tests.test_caller_tiers import (  # noqa: F401
    TestTheLadderLivesInOnePlace,
    TestATierIncludesTheOnesBelowIt,
    TestAnUnknownTierFailsClosed,
    TestTheDoorDecidesTheTier,
    TestUpgradingKeepsTheStationExactlyAsItWas,
)
from tests.test_call_record import (  # noqa: F401
    TestASwallowedRequestIsWrittenDown,
    TestCallPrivacy,
    TestCallRecord,
    TestOneUtteranceIsOneLineInTheRecord,
    TestStaleRecordsCanBeThrownAway,
    TestTheCallerGetsAVerdict,
    TestTheCallRecordHearsBothSides,
    TestTheCallRecordSaysWhoRang,
    TestTheRecordAndItsProblemsShareOneClock,
)
from tests.test_call_flow import (  # noqa: F401
    TestACallerWhoWasNeverHeardIsToldSo,
    TestALineThatFailsToGenerateIsStillSpoken,
    TestBackgroundWorkIsNotGarbageCollected,
    TestCallRecordTimestamps,
    TestCallStructure,
    TestTheBarReleaseEndsTheTurn,
    TestComingBackFromAirIsAnnounced,
    TestEndingACallDisconnectsTheCaller,
    TestNothingToSay,
    TestSilentCallIsRecorded,
    TestTheAirGuardHoldsTheCallDJBack,
    TestTheCloseReasonIsReadable,
    TestTheIdleClockDoesNotRunWhileTheDJIsHeldBack,
    TestTheSignOffIsHeardBeforeTheLineCloses,
)
from tests.test_tools_surface import (  # noqa: F401
    TestActionsAllHaveAReceipt,
    TestExposedSurface,
    TestStationActionResults,
    TestStationWideTools,
    TestTellingTheCallerWhenTheirSongPlays,
    TestTheDJDescribesRecordsItHasInformationAbout,
)
from tests.test_tools_logic import (  # noqa: F401
    TestAnUnconfirmedDeliveryDoesNotStartAClock,
    TestMainToolLogic,
)
from tests.test_music_tools import (  # noqa: F401
    TestALateMatchStillReachesTheCaller,
    TestAMoodIsNotASearch,
    TestSearchingForWhatTheCallerActuallySaid,
)
from tests.test_takeover import (  # noqa: F401
    TestCancellingATakeover,
    TestNamingAShowTheCallerSaid,
    TestPinningAShow,
    TestTheCallerCannotDoThisAllNight,
    TestTheStationEndpointsAreTheOnesUpstreamServes,
)
from tests.test_brain import (  # noqa: F401
    TestTheDJKnowsTheStationsShows,
    TestACallerCanBeToldNothingIsKept,
    TestBrainSplit,
    TestCallerContext,
    TestOneBadTrackCannotSwallowThePrompt,
    TestPromptAssembly,
    TestPrompts,
    TestTheConductHarnessCannotReachTheRealStation,
)
from tests.test_speech_filter import (  # noqa: F401
    TestATypedToolCallNeverReachesTheSpeaker,
    TestPunctuationIsSpokenNotSpelled,
    TestSpeechFilter,
)
from tests.test_station import (  # noqa: F401
    TestABadPlaylistStaysSmall,
    TestAFailedReadSaysWhyItFailed,
    TestStationConfig,
    TestTheCardCacheHasOneHome,
    TestTheDJKnowsWhoIsInTheBoothAndWhatTheShowPlays,
    TestTheDJKnowsWhoIsListening,
    TestTheHoldMatchesHowLongTheStationWillTalk,
    TestTheLiveShowRecordSurvivesTheScheduleLookup,
    TestTheStationLogSaysWhatWasSaid,
    TestTuneIn,
)
from tests.test_webhooks import (  # noqa: F401
    TestADeliveredPushIsProvedRatherThanAssumed,
    TestARefusedRegistrationSaysWhichFieldWasWrong,
    TestOtherWebhookRowsSurviveOurRegistration,
    TestOurWebhookRowKeepsItsIdentity,
    TestPointingAtANewStationRegistersAgain,
    TestTheRegistrationShapeIsTheOneTheStationReads,
    TestWeOnlyAskForEventsTheStationKnows,
)
from tests.test_voice import (  # noqa: F401
    TestABackendTooSlowToBeOnAPhoneCallSaysSo,
    TestADeclaredSampleRateIsMeasuredNotTrusted,
    TestAPersonaCanWearItsOwnEffect,
    TestAVoiceTheBackendCannotSpeakIsNotSilence,
    TestEveryPersonaIsCheckedNotOnlyTheOneOnAir,
    TestShippedAdaptersAreWellFormed,
    TestTheSttModelIsLoadedOnceForTheWholeProcess,
    TestTheVoiceCanLiveInTheUrl,
    TestVoiceDiscoveryIsNotHardcodedToOneShape,
    TestWhatTheBackendSaidReachesTheOperator,
)
from tests.test_voicemail import (  # noqa: F401
    TestAMessageIsNeverLost,
    TestAVoicemailIsACallEntryToo,
    TestEachPersonaCanHaveItsOwnLine,
    TestTheMachineHasATierDoor,
    TestTheStationAnswersWhenNobodyIsOnAir,
    TestTheLineHasModes,
    TestAFreshGreetingIsBudgeted,
    TestTheCeilingActuallyHangsUp,
    TestTheBeepCanBeTheOperators,
    TestTheBeepVerdictIsVisible,
    TestTheDjOnlySpeaksOnce,
    TestTheBeepIsACueNotAGate,
    TestGreetingClipsFollowWhatTheyWereRenderedFrom,
    TestTheBeepIsRealAudio,
    TestTheMachineAnswersThroughTheRightRefusals,
)
from tests.test_chat import (  # noqa: F401
    TestChatsEndInsteadOfAccumulating,
    TestTheTextLineHasADoor,
    TestTheTypedBrainIsTheSameBrainInADifferentRegister,
)
from tests.test_docs import (  # noqa: F401
    TestTheDocsKeepUpWithTheCode,
)
from tests.test_house_rules import (  # noqa: F401
    TestEverySkillWouldActuallyLoad,
    TestEveryTestClassIsAggregated,
    TestNewCodeDoesNotArriveUntested,
    TestNoFileGrowsWithoutSomebodyDeciding,
    TestTheCallHarnessOnlyDialsLocal,
    TestTheCommitGateIsStillWiredUp,
    TestTheLogKeepsTheLinesThatMatter,
    TestTheRoutingTableIsInOnePlace,
    TestTheSuiteIsNotQuietlyNotRunning,
    TestTheWrittenInstructionsStillDescribeTheCode,
)


if __name__ == "__main__":
    import unittest

    unittest.main(verbosity=2)
